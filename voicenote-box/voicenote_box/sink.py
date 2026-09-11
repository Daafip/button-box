"""What the proxy does with a finished utterance.

This is the whole behavioural difference between a spoken command and a voice
note, kept apart from the Wyoming plumbing so it can be tested directly.

    assist      -> the real transcript goes back to Assist; the WAV is
                   discarded (or kept briefly, for debugging)
    voicenote   -> the WAV is transcoded and queued for a contact, and a
                   canned transcript goes back so Assist speaks a
                   confirmation through the box's own speaker

Every refusal returns the *failed* sentence rather than the sent one. A voice
note that silently did not go anywhere is the worst outcome this box has.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from voicenote_box import audio
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.errors import VoicenoteError
from voicenote_box.mode import Mode
from voicenote_box.paths import OUTBOX_DIR, RECORDINGS_DIR
from voicenote_box.queue import MessageQueue


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    """What to hand back to the Assist pipeline."""

    transcript: str | None  # None means "use the real transcript"
    queued_id: int | None = None
    error: str | None = None


class NoteSink:
    def __init__(
        self,
        config: Config,
        queue: MessageQueue,
        contacts: ContactStore,
        recordings_dir: Path = RECORDINGS_DIR,
        outbox_dir: Path = OUTBOX_DIR,
        clock=time.time,
        transcode=audio.to_opus,
    ) -> None:
        self.config = config
        self.queue = queue
        self.contacts = contacts
        self.recordings_dir = Path(recordings_dir)
        self.outbox_dir = Path(outbox_dir)
        self._clock = clock
        self._transcode = transcode

    def start(self, mode: Mode, rate: int, width: int, channels: int) -> audio.Recording:
        """Open a WAV for this utterance, named after the mode it was captured in."""
        stamp = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y%m%d-%H%M%S")
        # The suffix keeps two utterances in the same second from sharing a
        # name, which would let one overwrite the other's queued audio.
        path = self.recordings_dir / f"{stamp}-{secrets.token_hex(3)}-{mode.name}.wav"
        return audio.Recording(path, rate=rate, width=width, channels=channels)

    def write(self, recording: audio.Recording, chunk: bytes) -> None:
        """Append audio, stopping at the recording cap.

        Only the file is capped. Audio keeps flowing to the real speech
        recogniser, so capping can never truncate a normal Assist command.
        """
        if recording.seconds < self.config.max_recording_seconds:
            recording.write(chunk)

    def complete(self, recording: audio.Recording, mode: Mode) -> Outcome:
        seconds = recording.seconds
        path = recording.close()
        if not mode.is_voicenote:
            if self.config.assist_recording_retention_seconds <= 0:
                path.unlink(missing_ok=True)
            return Outcome(transcript=None)
        try:
            queued_id = self._queue_note(path, mode, seconds)
        except VoicenoteError as error:
            log.warning("voice note refused: %s", error)
            path.unlink(missing_ok=True)
            return Outcome(transcript=self.config.failed_sentence, error=str(error))
        except Exception as error:
            # A full disk or a broken ffmpeg must not leave the recording
            # behind, and must still reach the speaker as a refusal.
            log.exception("voice note could not be queued")
            path.unlink(missing_ok=True)
            return Outcome(transcript=self.config.failed_sentence, error=str(error))
        log.info("queued voice note %s for %s (%.1fs)", queued_id, mode.recipient_id, seconds)
        return Outcome(transcript=self.config.sent_sentence, queued_id=queued_id)

    def abandon(self, recording: audio.Recording) -> None:
        recording.discard()

    def _queue_note(self, wav_path: Path, mode: Mode, seconds: float) -> int:
        # Re-check the allowlist here, not only when the mode flag was armed:
        # a recipient can be removed between arming and speaking.
        recipient = self.contacts.get(mode.recipient_id)
        if seconds < self.config.min_recording_seconds:
            raise VoicenoteError("recording too short")
        if self.config.rate_limit_per_hour > 0:
            recent = self.queue.outbound_since(self._clock() - 3600)
            if recent >= self.config.rate_limit_per_hour:
                raise VoicenoteError("send rate limit reached")
        # Recording onto a broken outbox lets someone pile up messages they
        # believe were delivered. Refuse until the failures are cleared.
        if self.queue.outbound_failed() > 0:
            raise VoicenoteError("earlier notes failed to send")
        ogg_path = self.outbox_dir / (wav_path.stem + ".ogg")
        self._transcode(wav_path, ogg_path)
        wav_path.unlink(missing_ok=True)
        return self.queue.enqueue_outbound(recipient.id, ogg_path, seconds)
