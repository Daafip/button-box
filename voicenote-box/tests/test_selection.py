import tempfile
import unittest
from pathlib import Path

from voicenote_box.contacts import ContactStore
from voicenote_box.errors import VoicenoteError
from voicenote_box.selection import Selection


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.contacts = ContactStore(root / "contacts.json")
        self.contacts.put("oma", "Oma", "telegram", "1", tags=["04a1b2"])
        self.contacts.put("papa", "Papa", "telegram", "2")
        self.selection = Selection(self.contacts, root / "selection.json")

    def tearDown(self):
        self.directory.cleanup()

    def test_nothing_is_selected_to_begin_with(self):
        self.assertIsNone(self.selection.current())

    def test_a_tag_selects_its_recipient(self):
        self.selection.select_by_tag("04A1B2")

        self.assertEqual(self.selection.current().id, "oma")

    def test_an_unmapped_tag_refuses_and_leaves_the_selection_alone(self):
        self.selection.select("papa")

        with self.assertRaises(VoicenoteError):
            self.selection.select_by_tag("deadbeef")

        self.assertEqual(self.selection.current().id, "papa")

    def test_a_label_from_the_select_entity_resolves(self):
        self.assertEqual(self.selection.select_by_label("Papa").id, "papa")
        self.assertEqual(self.selection.select_by_label(" oma ").id, "oma")

    def test_an_unknown_label_refuses(self):
        with self.assertRaises(VoicenoteError):
            self.selection.select_by_label("geen")

    def test_a_revoked_recipient_reads_back_as_nothing_selected(self):
        self.selection.select("oma")
        self.contacts.remove("oma")

        self.assertIsNone(self.selection.current())

    def test_the_selection_survives_a_restart(self):
        self.selection.select("oma")

        reopened = Selection(self.contacts, self.selection.path)

        self.assertEqual(reopened.current().id, "oma")

    def test_clearing_returns_the_box_to_refusing_to_send(self):
        self.selection.select("oma")
        self.selection.clear()

        self.assertIsNone(self.selection.current())


if __name__ == "__main__":
    unittest.main()
