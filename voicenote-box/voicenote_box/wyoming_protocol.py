"""The Wyoming wire format, over asyncio streams.

An event is one UTF-8 JSON line, optionally followed by ``data_length`` bytes
of additional JSON merged into ``data``, optionally followed by
``payload_length`` bytes of binary payload (audio, for our purposes).

Implemented here rather than pulled in as a dependency: it is a small, stable
format, and owning it lets the proxy be tested over in-memory streams with no
container and no Whisper.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


DESCRIBE = "describe"
INFO = "info"
TRANSCRIBE = "transcribe"
TRANSCRIPT = "transcript"
AUDIO_START = "audio-start"
AUDIO_CHUNK = "audio-chunk"
AUDIO_STOP = "audio-stop"


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)
    payload: bytes | None = None


async def read_event(reader: asyncio.StreamReader) -> Event | None:
    """Read one event, or None when the peer closed the connection."""
    line = await reader.readline()
    if not line:
        return None
    header = json.loads(line)
    data = header.get("data") or {}
    data_length = header.get("data_length")
    if data_length:
        data = {**data, **json.loads(await reader.readexactly(data_length))}
    payload_length = header.get("payload_length")
    payload = await reader.readexactly(payload_length) if payload_length else None
    return Event(type=header["type"], data=data, payload=payload)


async def write_event(writer: asyncio.StreamWriter, event: Event) -> None:
    header: dict = {"type": event.type, "data": event.data or {}}
    if event.payload:
        header["payload_length"] = len(event.payload)
    writer.write(json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n")
    if event.payload:
        writer.write(event.payload)
    await writer.drain()


def audio_format(data: dict) -> tuple[int, int, int]:
    """Extract (rate, width, channels), defaulting to Assist's 16 kHz mono."""
    return (
        int(data.get("rate") or 16000),
        int(data.get("width") or 2),
        int(data.get("channels") or 1),
    )
