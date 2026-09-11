"""Home Assistant entities, published over MQTT discovery.

The add-on owns the box's state, so it declares the entities rather than
asking the operator to hand-write them:

    sensor.<device>_unread          how many replies are waiting
    sensor.<device>_last_sender     who sent the most recent one
    sensor.<device>_activity        idle / recording / sending / error
    sensor.<device>_outbox          notes still queued to send
    button.<device>_play_next       play the oldest unread reply
    select.<device>_recipient       who a held button sends to

The recipient select is generated from the allowlist, so it can only ever
offer an enrolled contact. Its extra "geen" option is the fail-closed
default: with it chosen, holding the button refuses to record.

``paho.mqtt`` is imported lazily. Without it, or without a broker, the
publisher degrades to no-ops and the rest of the box keeps working.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from voicenote_box.config import Config


log = logging.getLogger(__name__)

NO_RECIPIENT = "geen"

IDLE = "idle"
RECORDING = "recording"
SENDING = "sending"
ERROR = "error"


@dataclass(frozen=True)
class Topics:
    device_id: str

    @property
    def base(self) -> str:
        return f"voicenote_box/{self.device_id}"

    @property
    def availability(self) -> str:
        return f"{self.base}/availability"

    @property
    def unread(self) -> str:
        return f"{self.base}/unread"

    @property
    def last_sender(self) -> str:
        return f"{self.base}/last_sender"

    @property
    def activity(self) -> str:
        return f"{self.base}/activity"

    @property
    def outbox(self) -> str:
        return f"{self.base}/outbox"

    @property
    def play_next(self) -> str:
        return f"{self.base}/play_next/set"

    @property
    def recipient(self) -> str:
        return f"{self.base}/recipient"

    @property
    def recipient_command(self) -> str:
        return f"{self.base}/recipient/set"


def _device(config: Config) -> dict:
    return {
        "identifiers": [config.device_id],
        "name": config.device_name,
        "manufacturer": "Voice Note Box",
        "model": "Assist satellite voice notes",
    }


def discovery_messages(config: Config, recipient_labels: dict[str, str]) -> list[tuple[str, dict]]:
    """Build the retained discovery payloads for every entity."""
    topics = Topics(config.device_id)
    device = _device(config)
    prefix = config.mqtt_discovery_prefix.rstrip("/")

    def entry(component: str, object_id: str, payload: dict) -> tuple[str, dict]:
        return (
            f"{prefix}/{component}/{config.device_id}/{object_id}/config",
            {
                "unique_id": f"{config.device_id}_{object_id}",
                "object_id": f"{config.device_id}_{object_id}",
                "availability_topic": topics.availability,
                "device": device,
                **payload,
            },
        )

    return [
        entry("sensor", "unread", {
            "name": "Unread voice notes",
            "state_topic": topics.unread,
            "icon": "mdi:voicemail",
            "state_class": "measurement",
        }),
        entry("sensor", "last_sender", {
            "name": "Last sender",
            "state_topic": topics.last_sender,
            "icon": "mdi:account-voice",
        }),
        entry("sensor", "activity", {
            "name": "Activity",
            "state_topic": topics.activity,
            "icon": "mdi:record-rec",
        }),
        entry("sensor", "outbox", {
            "name": "Outbox",
            "state_topic": topics.outbox,
            "icon": "mdi:tray-full",
            "state_class": "measurement",
            "json_attributes_topic": topics.outbox,
            "value_template": "{{ value_json.pending }}",
        }),
        entry("button", "play_next", {
            "name": "Play next voice note",
            "command_topic": topics.play_next,
            "icon": "mdi:play-circle",
        }),
        entry("select", "recipient", {
            "name": "Recipient",
            "state_topic": topics.recipient,
            "command_topic": topics.recipient_command,
            "icon": "mdi:account-arrow-right",
            "options": [NO_RECIPIENT, *sorted(recipient_labels.values())],
        }),
    ]


class Publisher:
    """Publishes state and routes the two command topics back to callbacks."""

    def __init__(self, config: Config, on_play_next=None, on_recipient=None) -> None:
        self.config = config
        self.topics = Topics(config.device_id)
        # Public so they can be attached after the application exists.
        self.on_play_next = on_play_next
        self.on_recipient = on_recipient
        self._client = None

    def connect(self) -> bool:
        try:
            from paho.mqtt import client as mqtt
        except ImportError:
            log.warning("paho-mqtt is not installed; no Home Assistant entities published")
            return False
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.config.device_id)
        if self.config.mqtt_username:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        # The broker announces the box as offline if this process dies, so the
        # entities go unavailable instead of showing a frozen last value.
        client.will_set(self.topics.availability, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        try:
            client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
        except OSError as error:
            log.warning("mqtt broker unreachable: %s", error)
            return False
        client.loop_start()
        self._client = client
        return True

    def close(self) -> None:
        if self._client is None:
            return
        self._publish(self.topics.availability, "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None

    def announce(self, recipient_labels: dict[str, str]) -> None:
        for topic, payload in discovery_messages(self.config, recipient_labels):
            self._publish(topic, json.dumps(payload), retain=True)
        self._publish(self.topics.availability, "online", retain=True)

    def publish_activity(self, activity: str) -> None:
        self._publish(self.topics.activity, activity, retain=True)

    def publish_queue(self, unread: int, last_sender: str | None, pending: int,
                      failed: int) -> None:
        self._publish(self.topics.unread, str(unread), retain=True)
        self._publish(self.topics.last_sender, last_sender or "-", retain=True)
        self._publish(
            self.topics.outbox,
            json.dumps({"pending": pending, "failed": failed}),
            retain=True,
        )

    def publish_recipient(self, label: str) -> None:
        self._publish(self.topics.recipient, label or NO_RECIPIENT, retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        client.subscribe([(self.topics.play_next, 0), (self.topics.recipient_command, 0)])

    def _on_message(self, client, userdata, message) -> None:
        payload = message.payload.decode("utf-8", "replace").strip()
        try:
            if message.topic == self.topics.play_next and self.on_play_next:
                self.on_play_next()
            elif message.topic == self.topics.recipient_command and self.on_recipient:
                self.on_recipient(payload)
        except Exception:  # a bad command must not drop the MQTT loop
            log.exception("mqtt command failed")

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self._client is None:
            return
        self._client.publish(topic, payload, retain=retain)
