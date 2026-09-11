"""Signal adapter, talking to a local ``signal-cli-rest-api`` container.

Needs a dedicated number linked as a device; the REST container holds that
session. Attachments go out base64-encoded in the send body, which is what
that API accepts.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.errors import TransportError
from voicenote_box.http_client import download, request_json
from voicenote_box.transports.base import IncomingMessage


log = logging.getLogger(__name__)


class SignalTransport:
    name = "signal"

    def __init__(self, config: Config) -> None:
        if not config.signal_number:
            raise TransportError("signal_number is not configured", permanent=True)
        self._api = config.signal_api_url.rstrip("/")
        self._number = config.signal_number
        self._headers = {}
        if config.signal_api_user:
            credentials = f"{config.signal_api_user}:{config.signal_api_password}"
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            self._headers["Authorization"] = f"Basic {token}"

    def send_voice(self, address: str, ogg_path: Path) -> str:
        encoded = base64.b64encode(Path(ogg_path).read_bytes()).decode("ascii")
        response = request_json(
            f"{self._api}/v2/send",
            method="POST",
            body={
                "number": self._number,
                "recipients": [address],
                "message": "",
                "base64_attachments": [f"data:audio/ogg;filename={Path(ogg_path).name};base64,{encoded}"],
            },
            headers=self._headers,
        )
        return str(response.get("timestamp", ""))

    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]:
        """Drain the receive endpoint.

        ``signal-cli`` deletes envelopes as it hands them over, so the server
        holds the read position and the local cursor stays unused. A download
        that fails afterwards cannot be rewound to, unlike Telegram's offset.
        """
        envelopes = request_json(
            f"{self._api}/v1/receive/{self._number}", headers=self._headers
        )
        if isinstance(envelopes, dict):
            envelopes = envelopes.get("envelopes") or []
        messages = []
        for entry in envelopes:
            messages.extend(self._voice_messages(entry))
        return messages, cursor

    def download(self, message: IncomingMessage, destination: Path) -> Path:
        return download(
            f"{self._api}/v1/attachments/{message.media_ref}", destination, headers=self._headers
        )

    @staticmethod
    def _voice_messages(entry: dict) -> list[IncomingMessage]:
        envelope = entry.get("envelope") or entry
        data_message = envelope.get("dataMessage") or {}
        sender = envelope.get("sourceNumber") or envelope.get("source") or ""
        name = envelope.get("sourceName") or sender or "Onbekend"
        timestamp = str(envelope.get("timestamp") or data_message.get("timestamp") or "")
        messages = []
        for attachment in data_message.get("attachments") or []:
            if not str(attachment.get("contentType", "")).startswith("audio/"):
                continue
            messages.append(
                IncomingMessage(
                    remote_id=f"{timestamp}:{attachment.get('id')}",
                    sender_address=sender,
                    sender_name=name,
                    media_ref=str(attachment.get("id")),
                    seconds=None,
                )
            )
        return messages
