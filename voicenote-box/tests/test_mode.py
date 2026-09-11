import tempfile
import unittest
from pathlib import Path

from voicenote_box.errors import VoicenoteError
from voicenote_box.mode import ASSIST, VOICENOTE, ModeStore


class Clock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value


class ModeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = ModeStore(Path(self.directory.name) / "state" / "mode.json", self.clock)

    def tearDown(self):
        self.directory.cleanup()

    def test_unset_flag_reads_as_assist(self):
        self.assertEqual(self.store.consume().name, ASSIST)

    def test_voicenote_requires_a_recipient(self):
        with self.assertRaises(VoicenoteError):
            self.store.set(VOICENOTE)
        with self.assertRaises(VoicenoteError):
            self.store.set(VOICENOTE, "")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(VoicenoteError):
            self.store.set("broadcast", "oma")

    def test_flag_captures_one_utterance_and_no_more(self):
        self.store.set(VOICENOTE, "oma")

        first = self.store.consume()
        second = self.store.consume()

        self.assertEqual((first.name, first.recipient_id), (VOICENOTE, "oma"))
        self.assertEqual(second.name, ASSIST)

    def test_peek_leaves_the_flag_armed(self):
        self.store.set(VOICENOTE, "oma")

        self.assertTrue(self.store.peek().is_voicenote)
        self.assertTrue(self.store.consume().is_voicenote)

    def test_flag_expires_so_a_lost_release_cannot_capture_later_speech(self):
        self.store.set(VOICENOTE, "oma", ttl=120)

        self.clock.value += 121

        self.assertEqual(self.store.consume().name, ASSIST)

    def test_clear_disarms_immediately(self):
        self.store.set(VOICENOTE, "oma")
        self.store.clear()

        self.assertEqual(self.store.consume().name, ASSIST)

    def test_flag_survives_a_restart_within_its_lifetime(self):
        self.store.set(VOICENOTE, "oma")

        reopened = ModeStore(self.store.path, self.clock)

        self.assertEqual(reopened.consume().recipient_id, "oma")

    def test_corrupt_flag_degrades_to_assist_so_the_pipeline_keeps_working(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)

        for content in (
            "{not json",
            '{"mode": "voicenote", "recipient_id": "oma", "expires_at": "soon"}',
            '{"mode": "voicenote", "recipient_id": "oma", "expires_at": {}}',
            "[]",
        ):
            with self.subTest(content=content):
                self.store.path.write_text(content)
                self.assertEqual(self.store.consume().name, ASSIST)


if __name__ == "__main__":
    unittest.main()
