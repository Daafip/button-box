"""Drains the outbound queue and fills the inbound one.

Runs on a worker thread. Both directions are deliberately conservative:

* an outbound note is only removed from the queue once the service accepted
  it, so an unplugged router produces a retry rather than a silent loss;
* an inbound cursor only advances past messages that were fully downloaded
  and queued, so a network failure mid-batch replays instead of skipping --
  bounded by ``MAX_MESSAGE_ATTEMPTS`` so one undownloadable message cannot
  wedge the queue forever.

Replay only works for a transport that has a cursor to rewind. Signal hands
envelopes over and deletes them, so there is nothing to go back to; for
those, a message that cannot be fetched is reported and the rest of the
batch is still processed, rather than dropping the batch as well.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from pathlib import Path

from voicenote_box import audio
from voicenote_box.activity import Activity
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.errors import TransportError, VoicenoteError
from voicenote_box.paths import INBOX_DIR
from voicenote_box.queue import MessageQueue
from voicenote_box.transports.base import IncomingMessage, Transport


log = logging.getLogger(__name__)

MAX_MESSAGE_ATTEMPTS = 5

STORED = "stored"    # queued for playback
SKIPPED = "skipped"  # deliberately not queued; do not fetch it again
RETRY = "retry"      # transient failure; leave the cursor before it

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(transport: str, remote_id: str) -> str:
    """Build a filename from service-supplied ids without trusting them."""
    return f"{_UNSAFE.sub('-', transport)}-{_UNSAFE.sub('-', remote_id)[:80]}"


class Courier:
    def __init__(
        self,
        config: Config,
        queue: MessageQueue,
        contacts: ContactStore,
        transport: Transport,
        activity: Activity | None = None,
        inbox_dir: Path = INBOX_DIR,
        clock=time.time,
        transcode=audio.to_playable_wav,
    ) -> None:
        self.config = config
        self.queue = queue
        self.contacts = contacts
        self.transport = transport
        self.activity = activity or Activity()
        self.inbox_dir = Path(inbox_dir)
        self._clock = clock
        self._transcode = transcode
        self._attempts: Counter[str] = Counter()

    # -- outbound ---------------------------------------------------------

    def send_due(self) -> int:
        """Attempt every due outbound note. Returns how many were delivered."""
        due = self.queue.due_outbound()
        if not due:
            self._refresh_activity()
            return 0
        self.activity.set_sending(True)
        delivered = 0
        try:
            for message in due:
                if self._send(message):
                    delivered += 1
        finally:
            self.activity.set_sending(False)
            self._refresh_activity()
        return delivered

    def _send(self, message) -> bool:
        try:
            recipient = self.contacts.get(message.recipient_id)
        except VoicenoteError as error:
            # The contact was removed after the note was recorded. Never fall
            # back to anyone else; fail the note and let the box show it.
            self.queue.mark_failed(message.id, str(error))
            log.error("note %s has no enrolled recipient: %s", message.id, error)
            return False
        if not message.path.exists():
            self.queue.mark_failed(message.id, "audio file is missing")
            log.error("note %s lost its audio file", message.id)
            return False
        try:
            remote_id = self.transport.send_voice(recipient.address, message.path)
        except TransportError as error:
            if error.permanent:
                self.queue.mark_failed(message.id, str(error))
                log.error("note %s permanently refused: %s", message.id, error)
            else:
                retry_at = self.queue.mark_retry(message.id, str(error))
                log.warning(
                    "note %s failed, retrying in %.0fs: %s",
                    message.id, retry_at - self._clock(), error,
                )
            return False
        self.queue.mark_sent(message.id, remote_id)
        log.info("sent note %s to %s", message.id, recipient.id)
        return True

    # -- inbound ----------------------------------------------------------

    def fetch_inbound(self) -> int:
        """Poll once. Returns how many new messages were queued."""
        try:
            messages, cursor = self.transport.poll(self.queue.cursor(self.transport.name))
        except TransportError as error:
            log.warning("poll failed: %s", error)
            self.activity.set_error(True)
            return 0
        queued = 0
        for message in messages:
            result = self._accept(message)
            if result == RETRY and not self._replay(message):
                self._refresh_activity()
                return queued
            self._attempts.pop(message.remote_id, None)
            if result == STORED:
                queued += 1
            self._store_cursor(message.cursor)
        self._store_cursor(cursor)
        self._refresh_activity()
        return queued

    def _accept(self, message: IncomingMessage) -> str:
        """Download, transcode and queue one message. Returns its outcome."""
        sender = self.contacts.resolve_address(self.transport.name, message.sender_address)
        if sender is None:
            # Same allowlist as sending: audio from a stranger is not queued
            # and the sender's address is kept out of the log.
            log.info("dropped audio from an address that is not enrolled")
            return SKIPPED
        stem = _safe_stem(self.transport.name, message.remote_id)
        suffix = message.suffix if message.suffix.startswith(".") else ".ogg"
        try:
            downloaded = self.transport.download(message, self.inbox_dir / f"{stem}{suffix}")
            playable = self._transcode(downloaded, self.inbox_dir / f"{stem}.wav")
        except (TransportError, VoicenoteError) as error:
            log.warning("could not fetch incoming audio: %s", error)
            return RETRY
        downloaded.unlink(missing_ok=True)
        seconds = message.seconds
        if seconds is None:
            seconds = audio.wav_seconds_or_none(playable)
        queued = self.queue.enqueue_inbound(
            self.transport.name, message.remote_id, sender.id, sender.label, playable, seconds
        )
        if queued is None:
            log.info("skipped a message that was already queued")
            return SKIPPED
        log.info("queued incoming note %s from %s", queued, sender.id)
        return STORED

    def _replay(self, message: IncomingMessage) -> bool:
        """Arrange for a message to come back. False means the batch stops here.

        With a cursor, rewinding to before this message replays it and
        everything after it, so there is no reason to go on. Without one
        there is nothing to rewind: stopping would lose the rest of the
        batch too, so the message is given up on and the batch continues.
        """
        if message.cursor is None:
            log.error("dropped an incoming message that could not be fetched")
            return True
        attempts = self._attempts[message.remote_id] + 1
        self._attempts[message.remote_id] = attempts
        if attempts < MAX_MESSAGE_ATTEMPTS:
            self._store_cursor(message.cursor, previous=True)
            return False
        log.error("giving up on a message after %s attempts", MAX_MESSAGE_ATTEMPTS)
        return True

    def _store_cursor(self, cursor: str | None, previous: bool = False) -> None:
        """Persist the resume point. ``previous`` rewinds to before ``cursor``."""
        if cursor is None:
            return
        if previous:
            try:
                cursor = str(int(cursor) - 1)
            except ValueError:
                return
        self.queue.set_cursor(self.transport.name, cursor)

    # -- state ------------------------------------------------------------

    def _refresh_activity(self) -> None:
        self.activity.set_error(self.queue.outbound_failed() > 0)
