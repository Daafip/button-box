"""The flag that decides what the proxy does with an utterance.

Home Assistant sets this over REST *before* the microphone opens, then starts
the voice assistant. The proxy latches the flag when the utterance begins and
immediately resets it to ``assist``.

Two properties keep a stale flag from turning an ordinary spoken command into
a voice note sent to a contact:

* it is one-shot -- latching consumes it, so it can capture one utterance and
  never the one after;
* it expires -- a button press whose release event was lost stops mattering
  after ``DEFAULT_TTL_SECONDS``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from voicenote_box.errors import VoicenoteError
from voicenote_box.paths import MODE_FILE
from voicenote_box.store import exclusive_lock, read_json, write_json


log = logging.getLogger(__name__)

ASSIST = "assist"
VOICENOTE = "voicenote"
MODES = frozenset({ASSIST, VOICENOTE})
DEFAULT_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class Mode:
    name: str
    recipient_id: str | None = None

    @property
    def is_voicenote(self) -> bool:
        return self.name == VOICENOTE


ASSIST_MODE = Mode(ASSIST)


class ModeStore:
    def __init__(self, path: Path = MODE_FILE, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    def set(
        self,
        name: str,
        recipient_id: str | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> Mode:
        """Arm the flag. ``voicenote`` without a recipient is rejected."""
        if name not in MODES:
            raise VoicenoteError(f"unknown mode {name!r}")
        if name == VOICENOTE and not recipient_id:
            raise VoicenoteError("voicenote mode requires a recipient")
        if name == ASSIST:
            recipient_id = None
        mode = Mode(name, recipient_id)
        with exclusive_lock(self.path):
            self._write(mode, expires_at=self._clock() + ttl)
        return mode

    def clear(self) -> None:
        with exclusive_lock(self.path):
            self._write(ASSIST_MODE, expires_at=0.0)

    def peek(self) -> Mode:
        """Report the armed mode without consuming it. For status displays."""
        with exclusive_lock(self.path):
            return self._read()

    def consume(self) -> Mode:
        """Latch the armed mode for one utterance and disarm.

        Called when the utterance starts, not when it ends: an aborted
        recording must not leave the flag armed for the next speaker.
        """
        with exclusive_lock(self.path):
            mode = self._read()
            if mode.is_voicenote:
                self._write(ASSIST_MODE, expires_at=0.0)
            return mode

    def _read(self) -> Mode:
        # Assist must keep working no matter what is in this file, and
        # "assist" is also the safe answer: it sends nothing to anyone. So
        # every way this can fail to parse ends in the same place.
        try:
            document = read_json(self.path)
            if not isinstance(document, dict):
                return ASSIST_MODE
            if float(document.get("expires_at") or 0.0) <= self._clock():
                return ASSIST_MODE
            name = document.get("mode")
            recipient_id = document.get("recipient_id")
        except (ValueError, TypeError):
            log.warning("mode flag is unreadable; treating as assist")
            return ASSIST_MODE
        if name != VOICENOTE or not isinstance(recipient_id, str) or not recipient_id:
            return ASSIST_MODE
        return Mode(VOICENOTE, recipient_id)

    def _write(self, mode: Mode, expires_at: float) -> None:
        write_json(
            self.path,
            {
                "mode": mode.name,
                "recipient_id": mode.recipient_id,
                "expires_at": expires_at,
                "set_at": self._clock(),
            },
        )
