"""Calls back into Home Assistant through the Supervisor's core proxy.

An add-on is given ``SUPERVISOR_TOKEN`` and can reach the core API at
``http://supervisor/core/api``. That is enough to announce a voice note on
the satellite without an automation in the middle, so a press of the play
button plays the note and nothing else has to be wired up.

Every failure here is non-fatal: the queue keeps the note, and the caller
reports it rather than crashing the add-on.
"""

from __future__ import annotations

import logging
import os

from voicenote_box.errors import TransportError
from voicenote_box.http_client import request_json


log = logging.getLogger(__name__)

CORE_API = os.environ.get("VOICENOTE_CORE_API", "http://supervisor/core/api")


class HomeAssistant:
    def __init__(self, token: str | None = None, base_url: str = CORE_API) -> None:
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.base_url = base_url.rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.token)

    def call_service(self, domain: str, service: str, data: dict) -> None:
        if not self.available:
            raise TransportError("no Supervisor token; cannot call Home Assistant")
        request_json(
            f"{self.base_url}/services/{domain}/{service}",
            method="POST",
            body=data,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def announce(self, entity_id: str, media_url: str) -> None:
        """Play a voice note on an Assist satellite.

        ``assist_satellite.announce`` ducks the satellite and returns when
        playback finishes, which ``media_player.play_media`` does not.
        """
        domain = entity_id.split(".", 1)[0]
        if domain == "assist_satellite":
            self.call_service(
                "assist_satellite", "announce",
                {"entity_id": entity_id, "media_id": media_url},
            )
            return
        self.call_service(
            "media_player", "play_media",
            {
                "entity_id": entity_id,
                "media_content_id": media_url,
                "media_content_type": "music",
                "announce": True,
            },
        )
