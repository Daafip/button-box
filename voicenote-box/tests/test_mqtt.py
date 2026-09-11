import json
import unittest

from voicenote_box.config import Config
from voicenote_box.mqtt import NO_RECIPIENT, Publisher, Topics, discovery_messages


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(device_id="box", device_name="Box", mqtt_discovery_prefix="ha")
        self.messages = dict(discovery_messages(self.config, {"oma": "Oma", "papa": "Papa"}))

    def test_every_entity_is_announced_under_the_discovery_prefix(self):
        self.assertEqual(
            sorted(self.messages),
            [
                "ha/button/box/play_next/config",
                "ha/select/box/recipient/config",
                "ha/sensor/box/activity/config",
                "ha/sensor/box/last_sender/config",
                "ha/sensor/box/outbox/config",
                "ha/sensor/box/unread/config",
            ],
        )

    def test_entities_share_one_device_so_they_group_in_the_ui(self):
        for payload in self.messages.values():
            self.assertEqual(payload["device"]["identifiers"], ["box"])
            self.assertEqual(payload["availability_topic"], "voicenote_box/box/availability")

    def test_unique_ids_are_distinct(self):
        ids = [payload["unique_id"] for payload in self.messages.values()]

        self.assertEqual(len(ids), len(set(ids)))

    def test_the_recipient_select_offers_only_enrolled_contacts_plus_none(self):
        options = self.messages["ha/select/box/recipient/config"]["options"]

        self.assertEqual(options, [NO_RECIPIENT, "Oma", "Papa"])

    def test_an_empty_allowlist_still_offers_the_fail_closed_option(self):
        messages = dict(discovery_messages(self.config, {}))

        self.assertEqual(messages["ha/select/box/recipient/config"]["options"], [NO_RECIPIENT])

    def test_payloads_are_serialisable(self):
        for payload in self.messages.values():
            self.assertIsInstance(json.dumps(payload), str)


class FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []
        self.will = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def will_set(self, topic, payload, retain=False):
        self.will = (topic, payload)

    def connect(self, host, port, keepalive=60):
        self.connected = (host, port)

    def loop_start(self):
        pass

    def subscribe(self, topics):
        self.subscribed.extend(topics)

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(device_id="box")
        self.topics = Topics("box")
        self.played = []
        self.chosen = []
        self.publisher = Publisher(
            self.config, on_play_next=lambda: self.played.append(True),
            on_recipient=self.chosen.append,
        )
        self.client = FakeClient()
        self.publisher._client = self.client

    def test_state_is_retained_so_entities_survive_a_broker_restart(self):
        self.publisher.publish_queue(2, "Oma", 1, 0)

        self.assertIn((self.topics.unread, "2", True), self.client.published)
        self.assertIn((self.topics.last_sender, "Oma", True), self.client.published)

    def test_a_quiet_box_reports_a_placeholder_sender(self):
        self.publisher.publish_queue(0, None, 0, 0)

        self.assertIn((self.topics.last_sender, "-", True), self.client.published)

    def test_the_outbox_sensor_carries_pending_and_failed_counts(self):
        self.publisher.publish_queue(0, None, 3, 1)

        payload = next(p for t, p, _ in self.client.published if t == self.topics.outbox)
        self.assertEqual(json.loads(payload), {"pending": 3, "failed": 1})

    def test_commands_reach_their_callbacks(self):
        self.publisher._on_connect(self.client, None, None, 0)
        self.assertEqual(
            [topic for topic, _ in self.client.subscribed],
            [self.topics.play_next, self.topics.recipient_command],
        )

        self.publisher._on_message(self.client, None, Message(self.topics.play_next, b"PRESS"))
        self.publisher._on_message(
            self.client, None, Message(self.topics.recipient_command, b"Oma")
        )

        self.assertEqual(self.played, [True])
        self.assertEqual(self.chosen, ["Oma"])

    def test_a_failing_command_is_logged_rather_than_dropping_the_loop(self):
        def explode():
            raise RuntimeError("boom")

        self.publisher.on_play_next = explode

        with self.assertLogs("voicenote_box.mqtt", "ERROR"):
            self.publisher._on_message(
                self.client, None, Message(self.topics.play_next, b"PRESS")
            )

    def test_publishing_without_a_broker_is_a_no_op(self):
        publisher = Publisher(self.config)

        publisher.publish_activity("recording")  # must not raise


class Message:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


if __name__ == "__main__":
    unittest.main()
