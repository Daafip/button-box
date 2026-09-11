"""One activity state out of several independent reporters.

The proxy knows when it is recording, the courier knows when it is sending
and whether anything is stuck. The LED shows one colour, so the states are
ranked: recording wins over sending, sending over error, error over idle.
"""

from __future__ import annotations

from voicenote_box.mqtt import ERROR, IDLE, RECORDING, SENDING


class Activity:
    def __init__(self, publish=None) -> None:
        self._publish = publish
        self._recording = False
        self._sending = False
        self._error = False
        self.state = IDLE

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._emit()

    def set_sending(self, sending: bool) -> None:
        self._sending = sending
        self._emit()

    def set_error(self, error: bool) -> None:
        self._error = error
        self._emit()

    def _emit(self) -> None:
        state = (
            RECORDING if self._recording
            else SENDING if self._sending
            else ERROR if self._error
            else IDLE
        )
        if state == self.state:
            return
        self.state = state
        if self._publish is not None:
            self._publish(state)
