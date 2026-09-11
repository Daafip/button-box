"""WhatsApp Business Platform (Cloud API) adapter -- optional, see docs.

This is the official API, and it is the wrong shape for a box that sends
spontaneously: free-form audio is only accepted inside the 24-hour window a
recipient opens by messaging the business number, and templates cannot carry
a voice note. Outside that window Meta rejects the send, and this adapter
surfaces that as a *permanent* failure rather than retrying forever.

Inbound needs a public HTTPS webhook, which an add-on behind a home router
does not have, so :meth:`poll` returns nothing and the queue stays empty.
"""

from __future__ import annotations

import logging
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.errors import TransportError
from voicenote_box.http_client import download, post_multipart, request_json
from voicenote_box.transports.base import IncomingMessage


log = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v21.0"
# Meta's code for "no open messaging window with this recipient".
OUTSIDE_WINDOW_CODE = 131047


class WhatsAppCloudTransport:
    name = "whatsapp_cloud"

    def __init__(self, config: Config) -> None:
        if not (config.whatsapp_token and config.whatsapp_phone_number_id):
            raise TransportError("whatsapp credentials are not configured", permanent=True)
        self._token = config.whatsapp_token
        self._phone_number_id = config.whatsapp_phone_number_id

    def send_voice(self, address: str, ogg_path: Path) -> str:
        media_id = self._upload(ogg_path)
        response = self._guard(
            lambda: request_json(
                f"{GRAPH_API}/{self._phone_number_id}/messages",
                method="POST",
                body={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    # Meta wants the number without a leading plus.
                    "to": address.lstrip("+"),
                    "type": "audio",
                    "audio": {"id": media_id},
                },
                headers=self._headers(),
            )
        )
        messages = response.get("messages") or []
        if not messages:
            raise TransportError("whatsapp returned no message id", permanent=True)
        return str(messages[0].get("id", ""))

    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]:
        return [], cursor

    def download(self, message: IncomingMessage, destination: Path) -> Path:
        response = self._guard(
            lambda: request_json(f"{GRAPH_API}/{message.media_ref}", headers=self._headers())
        )
        url = response.get("url")
        if not url:
            raise TransportError("whatsapp did not return a media url", permanent=True)
        return self._guard(lambda: download(url, destination, headers=self._headers()))

    def _upload(self, ogg_path: Path) -> str:
        response = self._guard(
            lambda: post_multipart(
                f"{GRAPH_API}/{self._phone_number_id}/media",
                {"messaging_product": "whatsapp", "type": "audio/ogg"},
                "file",
                ogg_path,
                headers=self._headers(),
            )
        )
        media_id = response.get("id")
        if not media_id:
            raise TransportError("whatsapp media upload returned no id", permanent=True)
        return str(media_id)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _guard(self, call):
        try:
            return call()
        except TransportError as error:
            message = str(error).replace(self._token, "***")
            if str(OUTSIDE_WINDOW_CODE) in message:
                raise TransportError(
                    "no open 24-hour messaging window with this recipient", permanent=True
                ) from None
            raise TransportError(message, permanent=error.permanent) from None
