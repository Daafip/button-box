"""The shipped ESPHome and Home Assistant files must match what is published.

These files reference entities by id. An id that no longer exists produces
an ESPHome sensor that never updates and a template that renders "unknown"
-- both silent. The contract is checked here instead.
"""

import re
import unittest
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.mqtt import discovery_messages


ROOT = Path(__file__).resolve().parent.parent
ENTITY = re.compile(r"\b((?:sensor|button|select|binary_sensor)\.voicenote_box_[a-z_]+)")


def published_entities() -> set[str]:
    return {
        f"{topic.split('/')[1]}.{payload['object_id']}"
        for topic, payload in discovery_messages(Config(), {"oma": "Oma"})
    }


def referenced_entities() -> dict[str, set[str]]:
    sources = list((ROOT / "esphome").glob("*.yaml"))
    sources += list((ROOT / "homeassistant").rglob("*.yaml"))
    return {
        str(path.relative_to(ROOT)): set(ENTITY.findall(path.read_text()))
        for path in sources
    }


class WiringTests(unittest.TestCase):
    def test_every_referenced_entity_is_actually_published(self):
        published = published_entities()

        for source, referenced in referenced_entities().items():
            with self.subTest(source=source):
                self.assertEqual(referenced - published, set())

    def test_the_shipped_files_do_reference_the_box(self):
        # Guards against the regex silently matching nothing after a rename.
        everything = set().union(*referenced_entities().values())

        self.assertIn("sensor.voicenote_box_unread", everything)
        self.assertIn("button.voicenote_box_play_next", everything)

    def test_the_spoken_confirmations_match_the_custom_sentences(self):
        sentences = (ROOT / "homeassistant/sentences/nl/voicenote.yaml").read_text()
        config = Config()

        # Assist answers "dat begreep ik niet" if these ever drift apart.
        self.assertIn(f'"{config.sent_sentence}"', sentences)
        self.assertIn(f'"{config.failed_sentence}"', sentences)


if __name__ == "__main__":
    unittest.main()
