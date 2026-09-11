"""Recording sink and ffmpeg transcodes.

Outbound notes leave as OGG/Opus because that is what messaging services
accept as a native voice note. Inbound audio is converted to WAV so the
satellite's media player can stream it without a codec surprise.
"""

from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path

from voicenote_box.errors import VoicenoteError


FFMPEG = os.environ.get("VOICENOTE_FFMPEG", "ffmpeg")
TRANSCODE_TIMEOUT = 120

# Cuts low-mid mud and lifts presence for the small speakers these boxes use,
# then normalizes loudness so one sender is not twice as loud as the next.
# Set VOICENOTE_EQ_FILTER="" to play incoming audio untouched.
EQ_FILTER = os.environ.get(
    "VOICENOTE_EQ_FILTER",
    "highpass=f=150,treble=g=6:f=3000,loudnorm=I=-16:TP=-1.5",
)


class Recording:
    """Accumulates PCM chunks from one utterance into a WAV file.

    The file is written to a ``.part`` name and renamed on ``close`` so a
    crash mid-utterance never leaves a truncated WAV looking like a complete
    voice note ready to send.
    """

    def __init__(self, path: Path, rate: int = 16000, width: int = 2, channels: int = 1) -> None:
        self.path = Path(path)
        self.rate = rate
        self.width = width
        self.channels = channels
        self.frames = 0
        self._partial = self.path.with_name(self.path.name + ".part")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self._partial), "wb")
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(width)
        self._wave.setframerate(rate)

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def write(self, chunk: bytes) -> None:
        self._wave.writeframes(chunk)
        self.frames += len(chunk) // (self.width * self.channels)

    def close(self) -> Path:
        self._wave.close()
        os.replace(self._partial, self.path)
        return self.path

    def discard(self) -> None:
        self._wave.close()
        self._partial.unlink(missing_ok=True)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        return handle.getnframes() / rate if rate else 0.0


def wav_seconds_or_none(path: Path) -> float | None:
    """Duration, or None when the file cannot be read as a WAV.

    ``wave`` raises ``wave.Error`` and ``EOFError``, neither of which is an
    OSError, so callers that only want the number ask for it here.
    """
    try:
        return wav_seconds(path)
    except (OSError, EOFError, wave.Error):
        return None


def _ffmpeg(arguments: list[str], run=subprocess.run) -> None:
    command = [FFMPEG, "-y", "-loglevel", "error", *arguments]
    try:
        result = run(command, capture_output=True, text=True, timeout=TRANSCODE_TIMEOUT)
    except FileNotFoundError as error:
        raise VoicenoteError("ffmpeg is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise VoicenoteError("transcode timed out") from error
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise VoicenoteError(f"transcode failed: {detail[-1] if detail else 'unknown error'}")


def to_opus(source: Path, destination: Path, run=subprocess.run) -> Path:
    """Transcode to the OGG/Opus mono voice note messaging services expect."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    _ffmpeg(
        [
            "-i", str(source),
            "-c:a", "libopus",
            "-b:a", "24k",
            "-ar", "48000",
            "-ac", "1",
            "-f", "ogg",
            str(partial),
        ],
        run=run,
    )
    os.replace(partial, destination)
    return destination


def to_playable_wav(source: Path, destination: Path, run=subprocess.run) -> Path:
    """Transcode received audio to WAV, shaped for a small box speaker."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    filters = ["-af", EQ_FILTER] if EQ_FILTER else []
    _ffmpeg(
        [
            "-i", str(source),
            *filters,
            "-ar", "48000",
            "-ac", "1",
            "-f", "wav",
            str(partial),
        ],
        run=run,
    )
    os.replace(partial, destination)
    return destination
