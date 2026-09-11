"""The add-on manifest and Config must describe the same options.

An option Home Assistant renders but Config does not know is silently
ignored; a Config field the manifest never exposes cannot be set at all.
Both fail quietly, so they are checked here.
"""

import unittest
from dataclasses import fields
from pathlib import Path

from voicenote_box.config import Config

try:
    import yaml
except ImportError:  # PyYAML is not a project dependency
    yaml = None


MANIFEST = Path(__file__).resolve().parent.parent / "config.yaml"


@unittest.skipUnless(yaml is not None, "PyYAML is not installed")
class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = yaml.safe_load(MANIFEST.read_text())

    def test_every_option_has_a_schema_entry(self):
        self.assertEqual(set(self.manifest["options"]), set(self.manifest["schema"]))

    def test_every_option_is_a_real_configuration_field(self):
        known = {entry.name for entry in fields(Config)}

        self.assertEqual(set(self.manifest["options"]) - known, set())

    def test_the_declared_options_parse_without_unknowns(self):
        config = Config.from_options(self.manifest["options"])

        self.assertEqual(config.unknown_options, ())

    def test_the_published_port_matches_the_control_api(self):
        ports = self.manifest["ports"]

        self.assertIn(f"{Config().api_port}/tcp", ports)
        self.assertIn(f"{Config().wyoming_port}/tcp", ports)

    def test_the_ingress_port_is_not_published_to_the_host(self):
        # Ingress traffic is already authenticated by Home Assistant; that
        # server trusts its callers, so it must not be reachable directly.
        self.assertEqual(self.manifest["ingress_port"], Config().ingress_port)
        self.assertNotIn(f"{Config().ingress_port}/tcp", self.manifest["ports"])

    def test_the_add_on_may_call_the_core_api_for_announcements(self):
        self.assertTrue(self.manifest["homeassistant_api"])


if __name__ == "__main__":
    unittest.main()
