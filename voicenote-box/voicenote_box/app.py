"""The operations the HTTP API, MQTT commands and the proxy all share.

One object owns the wiring so there is a single place where "arm the mode
flag", "choose a recipient" and "play the next reply" are defined, and the
two front ends stay thin.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import socket
from pathlib import Path

from voicenote_box.activity import Activity
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore, Recipient
from voicenote_box.errors import TransportError, VoicenoteError
from voicenote_box.hass import HomeAssistant
from voicenote_box.mode import ASSIST, VOICENOTE, ModeStore
from voicenote_box.paths import STATE_DIR
from voicenote_box.queue import MessageQueue
from voicenote_box.selection import Selection


log = logging.getLogger(__name__)

MEDIA_KEY_FILE = STATE_DIR / "media.key"


def media_key(path: Path = MEDIA_KEY_FILE) -> bytes:
    """Load, or create, the key that signs media URLs.

    Satellites fetch media over plain HTTP with no way to send a header, so
    the URL itself carries the proof. Deleting this file invalidates every
    previously issued link.
    """
    path = Path(path)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        # 0o600 before any content: the key must never be world-readable.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key


class Application:
    def __init__(
        self,
        config: Config,
        contacts: ContactStore,
        selection: Selection,
        modes: ModeStore,
        queue: MessageQueue,
        activity: Activity,
        hass: HomeAssistant | None = None,
        publisher=None,
        key: bytes | None = None,
    ) -> None:
        self.config = config
        self.contacts = contacts
        self.selection = selection
        self.modes = modes
        self.queue = queue
        self.activity = activity
        self.hass = hass or HomeAssistant()
        self.publisher = publisher
        self._key = key if key is not None else media_key()

    # -- recording --------------------------------------------------------

    def arm(self, mode: str, recipient_id: str | None = None) -> dict:
        """Arm the mode flag before the microphone opens.

        With no recipient in the request the currently selected one is used.
        Neither present is a refusal, never a default.
        """
        if mode == ASSIST:
            self.modes.clear()
            return {"mode": ASSIST}
        if mode != VOICENOTE:
            raise VoicenoteError(f"unknown mode {mode!r}")
        recipient = (
            self.contacts.get(recipient_id) if recipient_id else self.selection.current()
        )
        if recipient is None:
            raise VoicenoteError("no recipient selected")
        self.modes.set(VOICENOTE, recipient.id)
        return {"mode": VOICENOTE, "recipient": recipient.id, "label": recipient.label}

    # -- recipients -------------------------------------------------------

    def select(self, recipient_id: str | None = None, tag: str | None = None,
               label: str | None = None) -> Recipient | None:
        if tag:
            recipient = self.selection.select_by_tag(tag)
        elif recipient_id:
            recipient = self.selection.select(recipient_id)
        elif label:
            recipient = self.selection.select_by_label(label)
        else:
            self.selection.clear()
            self.publish_state()
            return None
        self.publish_state()
        return recipient

    def republish_entities(self) -> None:
        if self.publisher is None:
            return
        labels = {key: value.label for key, value in self.contacts.load().items()}
        self.publisher.announce(labels)
        self.publish_state()

    # -- playback ---------------------------------------------------------

    def play_next(self) -> dict | None:
        """Announce the oldest unread reply and mark it read.

        Marked read only after Home Assistant accepted the announcement, so a
        satellite that is offline leaves the light pulsing.
        """
        message = self.queue.next_unread()
        if message is None:
            return None
        entity = self.config.announce_entity or self.config.media_player_entity
        if not entity:
            raise VoicenoteError("no announce_entity configured")
        url = self.media_url("inbox", message.id)
        try:
            self.hass.announce(entity, url)
        except TransportError as error:
            self.activity.set_error(True)
            raise VoicenoteError(f"playback failed: {error}") from None
        self.queue.mark_read(message.id)
        self.publish_state()
        return {"id": message.id, "sender": message.sender_label, "media_url": url}

    def mark_read(self, message_id: int) -> None:
        self.queue.mark_read(message_id)
        self.publish_state()

    # -- media links ------------------------------------------------------

    def media_signature(self, kind: str, message_id: int) -> str:
        return hmac.new(self._key, f"{kind}/{message_id}".encode(), hashlib.sha256).hexdigest()[:32]

    def media_signature_valid(self, kind: str, message_id: int, signature: str) -> bool:
        return hmac.compare_digest(self.media_signature(kind, message_id), signature or "")

    def media_url(self, kind: str, message_id: int) -> str:
        base = self.config.media_base_url or f"http://{socket.gethostname()}:{self.config.api_port}"
        return (
            f"{base.rstrip('/')}/media/{kind}/{message_id}"
            f"?t={self.media_signature(kind, message_id)}"
        )

    # -- state ------------------------------------------------------------

    def state(self) -> dict:
        selected = self.selection.current()
        return {
            "activity": self.activity.state,
            "mode": self.modes.peek().name,
            "recipient": selected.id if selected else None,
            "recipient_label": selected.label if selected else None,
            "unread": self.queue.unread_count(),
            "last_sender": self.queue.last_sender(),
            "outbox_pending": self.queue.outbound_pending(),
            "outbox_failed": self.queue.outbound_failed(),
            "transport": self.config.transport,
        }

    def publish_state(self) -> None:
        if self.publisher is None:
            return
        snapshot = self.state()
        self.publisher.publish_queue(
            snapshot["unread"],
            snapshot["last_sender"],
            snapshot["outbox_pending"],
            snapshot["outbox_failed"],
        )
        self.publisher.publish_recipient(snapshot["recipient_label"] or "")
