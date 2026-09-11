"""Messaging service adapters behind one interface."""

from __future__ import annotations

from voicenote_box.config import Config
from voicenote_box.errors import VoicenoteError
from voicenote_box.transports.base import IncomingMessage, Transport


def build(config: Config) -> Transport:
    """Instantiate the configured transport."""
    if config.transport == "telegram":
        from voicenote_box.transports.telegram import TelegramTransport

        return TelegramTransport(config)
    if config.transport == "signal":
        from voicenote_box.transports.signal import SignalTransport

        return SignalTransport(config)
    if config.transport == "wacli":
        from voicenote_box.transports.wacli import WacliTransport

        return WacliTransport(config)
    if config.transport == "whatsapp_cloud":
        from voicenote_box.transports.whatsapp_cloud import WhatsAppCloudTransport

        return WhatsAppCloudTransport(config)
    raise VoicenoteError(f"unknown transport {config.transport!r}")


__all__ = ["IncomingMessage", "Transport", "build"]
