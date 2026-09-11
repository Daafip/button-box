import json
import tempfile
import unittest
from pathlib import Path

from voicenote_box.config import Config


class ConfigTests(unittest.TestCase):
    def test_defaults_describe_a_telegram_box(self):
        config = Config.from_options({})

        self.assertEqual(config.transport, "telegram")
        self.assertEqual(config.wyoming_port, 10400)
        self.assertEqual(config.upstream_stt_host, "core-whisper")

    def test_numbers_from_the_options_file_are_typed(self):
        config = Config.from_options({"max_recording_seconds": "30",
                                      "poll_interval_seconds": "2.5"})

        self.assertEqual(config.max_recording_seconds, 30)
        self.assertEqual(config.poll_interval_seconds, 2.5)

    def test_the_environment_overrides_the_options_file(self):
        config = Config.from_options(
            {"transport": "telegram"}, {"VOICENOTE_TRANSPORT": "signal"}
        )

        self.assertEqual(config.transport, "signal")

    def test_blank_options_fall_back_to_the_default(self):
        config = Config.from_options({"upstream_stt_host": ""})

        self.assertEqual(config.upstream_stt_host, "core-whisper")

    def test_unknown_options_are_reported_rather_than_silently_ignored(self):
        config = Config.from_options({"telegrm_token": "typo"})

        self.assertEqual(config.unknown_options, ("telegrm_token",))

    def test_secrets_are_masked_when_the_configuration_is_shown(self):
        config = Config.from_options({"telegram_token": "SECRET", "device_name": "Box"})

        shown = config.redacted()
        self.assertEqual(shown["telegram_token"], "***")
        self.assertEqual(shown["device_name"], "Box")
        self.assertNotIn("SECRET", json.dumps(shown))

    def test_an_unset_secret_is_shown_as_empty_not_masked(self):
        self.assertEqual(Config.from_options({}).redacted()["telegram_token"], "")

    def test_a_missing_options_file_yields_defaults(self):
        with tempfile.TemporaryDirectory() as name:
            config = Config.load(Path(name) / "absent.json", {})

        self.assertEqual(config.transport, "telegram")

    def test_options_are_read_from_the_add_on_file(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "options.json"
            path.write_text(json.dumps({"device_name": "Keuken"}))

            self.assertEqual(Config.load(path, {}).device_name, "Keuken")


if __name__ == "__main__":
    unittest.main()
