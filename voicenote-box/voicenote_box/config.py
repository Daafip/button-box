"""Add-on options.

Home Assistant renders the schema in ``addon/config.yaml`` and writes the
result to ``/data/options.json``. Environment variables override individual
values so the add-on can also be run directly during development.

Secrets live on this object and are never written to the log; ``redacted``
produces the form that is safe to print or expose through the ingress UI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path


OPTIONS_FILE = Path(os.environ.get("VOICENOTE_OPTIONS_FILE", "/data/options.json"))

SECRET_FIELDS = frozenset(
    {"api_token", "telegram_token", "signal_api_password", "whatsapp_token", "mqtt_password"}
)


@dataclass
class Config:
    # Wyoming
    wyoming_port: int = 10400
    upstream_stt_host: str = "core-whisper"
    upstream_stt_port: int = 10300

    # Control API and media serving
    api_port: int = 8099
    ingress_port: int = 8098
    api_token: str = ""
    media_base_url: str = ""

    # Spoken confirmations. These are the transcripts the proxy returns to the
    # pipeline instead of the real one; Assist matches them as custom
    # sentences and the intent script speaks the reply on the box.
    sent_sentence: str = "stuur spraakbericht"
    failed_sentence: str = "spraakbericht mislukt"

    # Playback. The add-on announces through the satellite itself when the
    # play button is pressed; media_player_entity is the fallback for boxes
    # whose satellite speaker is too tinny for voice notes.
    announce_entity: str = ""
    media_player_entity: str = ""

    # Safety limits
    max_recording_seconds: int = 60
    min_recording_seconds: float = 0.7
    rate_limit_per_hour: int = 20
    assist_recording_retention_seconds: int = 0
    settled_retention_days: int = 14

    # Transport
    transport: str = "telegram"
    telegram_token: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    signal_api_url: str = "http://localhost:8080"
    signal_number: str = ""
    signal_api_user: str = ""
    signal_api_password: str = ""
    wacli_binary: str = "/usr/local/bin/wacli"
    wacli_lock_wait: str = "60s"
    whatsapp_phone_number_id: str = ""
    whatsapp_token: str = ""
    poll_interval_seconds: float = 5.0

    # MQTT discovery
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_discovery_prefix: str = "homeassistant"
    device_id: str = "voicenote_box"
    device_name: str = "Voice Note Box"

    log_level: str = "INFO"

    unknown_options: tuple[str, ...] = field(default=(), repr=False)

    @classmethod
    def load(cls, path: Path = OPTIONS_FILE, environ=None) -> "Config":
        environ = os.environ if environ is None else environ
        try:
            options = json.loads(Path(path).read_text())
        except FileNotFoundError:
            options = {}
        if not isinstance(options, dict):
            options = {}
        return cls.from_options(options, environ)

    @classmethod
    def from_options(cls, options: dict, environ=None) -> "Config":
        environ = {} if environ is None else environ
        known = {entry.name: entry for entry in fields(cls) if entry.name != "unknown_options"}
        values = {}
        for name, entry in known.items():
            raw = environ.get(f"VOICENOTE_{name.upper()}", options.get(name))
            if raw is None or raw == "":
                continue
            values[name] = _coerce(entry.type, raw)
        unknown = tuple(sorted(set(options) - set(known)))
        return cls(**values, unknown_options=unknown)

    def redacted(self) -> dict:
        return {
            entry.name: ("***" if entry.name in SECRET_FIELDS and getattr(self, entry.name) else
                         getattr(self, entry.name))
            for entry in fields(self)
            if entry.name != "unknown_options"
        }


def _coerce(annotation, raw):
    if annotation == "int":
        return int(raw)
    if annotation == "float":
        return float(raw)
    return str(raw)
