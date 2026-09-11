"""WhatsApp through a local `wacli` (whatsmeow) session.

This is the closest thing to real WhatsApp and the transport the Raspberry
Pi Button Box uses. It is an **unofficial client**: Meta can ban the number,
and it breaks whenever the protocol changes.

Never link a personal number. Give the box a dedicated SIM or eSIM, and
expect to relink it occasionally.

Unlike the other adapters this one shells out to a binary rather than
speaking HTTP, so the subprocess runner is injectable and every call goes
through one place that never logs the command output -- it contains chat
identifiers and message ids.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.errors import TransportError
from voicenote_box.transports.base import IncomingMessage


log = logging.getLogger(__name__)

TIMEOUT = 300
LIST_LIMIT = 20


class WacliTransport:
    name = "wacli"

    def __init__(self, config: Config, run=subprocess.run) -> None:
        self._binary = config.wacli_binary
        self._lock_wait = config.wacli_lock_wait
        self._run = run

    def send_voice(self, address: str, ogg_path: Path) -> str:
        result = self._wacli(
            "send", "voice", "--file", str(ogg_path), "--to", address
        )
        return str((result.get("data") or {}).get("id") or "")

    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]:
        """List recent messages and report the audio ones.

        wacli has no cursor of its own, so the queue's own de-duplication on
        message id is what stops a reply being played twice.
        """
        result = self._wacli("messages", "list", "--limit", str(LIST_LIMIT), "--full")
        messages = []
        for entry in reversed((result.get("data") or {}).get("messages") or []):
            message = self._voice_message(entry)
            if message is not None:
                messages.append(message)
        return messages, cursor

    def download(self, message: IncomingMessage, destination: Path) -> Path:
        chat, _, message_id = message.media_ref.partition("|")
        result = self._wacli("media", "download", "--chat", chat, "--id", message_id)
        source = Path((result.get("data") or {}).get("path") or "")
        if not source.is_file():
            raise TransportError("wacli did not produce a media file")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return destination

    @staticmethod
    def _voice_message(entry: dict) -> IncomingMessage | None:
        if entry.get("FromMe") or entry.get("MediaType") != "audio":
            return None
        chat = entry.get("ChatJID")
        message_id = entry.get("MsgID")
        if not chat or not message_id:
            return None
        return IncomingMessage(
            remote_id=str(message_id),
            sender_address=str(chat),
            sender_name=str(entry.get("SenderName") or "Onbekend"),
            # download() needs both halves; the queue only stores remote_id.
            media_ref=f"{chat}|{message_id}",
        )

    def _wacli(self, *arguments: str) -> dict:
        command = [self._binary, *arguments, "--lock-wait", self._lock_wait, "--json"]
        try:
            result = self._run(command, capture_output=True, text=True, timeout=TIMEOUT)
        except FileNotFoundError as error:
            raise TransportError(f"{self._binary} is not installed", permanent=True) from error
        except subprocess.TimeoutExpired as error:
            raise TransportError("wacli timed out") from error
        if result.returncode != 0:
            # stderr carries chat identifiers and message ids; report the
            # command that failed, not what it said about whom.
            raise TransportError(f"wacli {arguments[0]} failed with status {result.returncode}")
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError as error:
            raise TransportError("wacli returned malformed output") from error
        if payload.get("success") is False:
            raise TransportError(f"wacli {arguments[0]} was refused")
        return payload
