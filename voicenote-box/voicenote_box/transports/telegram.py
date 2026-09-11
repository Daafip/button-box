"""Telegram Bot API adapter -- the v1 transport.

Voice notes are one ``sendVoice`` call, inbound arrives through ``getUpdates``
long polling, and no phone number or business verification is involved.

The bot token is part of every URL, so errors are scrubbed before they reach a
log line or the queue's ``last_error`` column.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.errors import TransportError
from voicenote_box.http_client import download, post_multipart, request_json
from voicenote_box.transports.base import IncomingMessage


log = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 25


class TelegramTransport:
    name = "telegram"

    def __init__(self, config: Config) -> None:
        if not config.telegram_token:
            raise TransportError("telegram_token is not configured", permanent=True)
        self._token = config.telegram_token
        self._api = config.telegram_api_base.rstrip("/")

    def send_voice(self, address: str, ogg_path: Path) -> str:
        response = self._guard(
            lambda: post_multipart(
                self._url("sendVoice"),
                {"chat_id": address},
                "voice",
                ogg_path,
            )
        )
        return str(self._result(response).get("message_id", ""))

    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]:
        query = {
            "timeout": POLL_TIMEOUT_SECONDS,
            "allowed_updates": json.dumps(["message"]),
        }
        if cursor:
            query["offset"] = cursor
        parameters = "&".join(f"{key}={value}" for key, value in query.items())
        response = self._guard(
            lambda: request_json(
                f"{self._url('getUpdates')}?{parameters}",
                timeout=POLL_TIMEOUT_SECONDS + 15,
            )
        )
        messages = []
        next_cursor = cursor
        for update in response.get("result") or []:
            next_cursor = str(int(update["update_id"]) + 1)
            message = self._voice_message(update.get("message") or {}, next_cursor)
            if message is not None:
                messages.append(message)
        return messages, next_cursor

    def download(self, message: IncomingMessage, destination: Path) -> Path:
        response = self._guard(
            lambda: request_json(f"{self._url('getFile')}?file_id={message.media_ref}")
        )
        remote_path = self._result(response).get("file_path")
        if not remote_path:
            raise TransportError("telegram did not return a file path", permanent=True)
        return self._guard(
            lambda: download(f"{self._api}/file/bot{self._token}/{remote_path}", destination)
        )

    @staticmethod
    def _voice_message(message: dict, cursor: str) -> IncomingMessage | None:
        audio = message.get("voice") or message.get("audio")
        if not audio or not audio.get("file_id"):
            return None
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        name = (
            sender.get("first_name")
            or sender.get("username")
            or chat.get("title")
            or "Onbekend"
        )
        return IncomingMessage(
            remote_id=str(message.get("message_id")),
            sender_address=str(chat.get("id")),
            sender_name=name,
            media_ref=audio["file_id"],
            cursor=cursor,
            seconds=audio.get("duration"),
        )

    @staticmethod
    def _result(response: dict) -> dict:
        if not response.get("ok"):
            raise TransportError(
                f"telegram refused: {response.get('description', 'unknown')}", permanent=True
            )
        return response.get("result") or {}

    def _url(self, method: str) -> str:
        return f"{self._api}/bot{self._token}/{method}"

    def _guard(self, call):
        """Run a call, stripping the bot token out of any error it raises."""
        try:
            return call()
        except TransportError as error:
            raise TransportError(
                str(error).replace(self._token, "***"), permanent=error.permanent
            ) from None
