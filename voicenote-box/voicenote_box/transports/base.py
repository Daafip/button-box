"""The interface every messaging service adapter implements.

Adapters are synchronous: they are plain HTTP calls, and the sender and poller
run them on a worker thread so the Wyoming proxy's event loop is never blocked.

An adapter raises :class:`TransportError` at its network boundary and nothing
else. ``permanent=True`` means the service will never accept this request, and
the queue should stop retrying it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class IncomingMessage:
    """One received voice message, before it is matched against the allowlist."""

    remote_id: str
    sender_address: str
    sender_name: str
    media_ref: str
    cursor: str | None = None
    seconds: float | None = None
    suffix: str = ".ogg"


@runtime_checkable
class Transport(Protocol):
    name: str

    def send_voice(self, address: str, ogg_path: Path) -> str:
        """Deliver a voice note. Returns the service's message id."""

    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]:
        """Fetch new voice messages and the cursor to resume from."""

    def download(self, message: IncomingMessage, destination: Path) -> Path:
        """Fetch the audio for a polled message."""
