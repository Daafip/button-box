import tempfile
import unittest
from pathlib import Path

from voicenote_box.contacts import ContactStore
from voicenote_box.errors import VoicenoteError


class ContactStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ContactStore(Path(self.directory.name) / "contacts.json")
        self.store.put("oma", "Oma", "telegram", "123456789", tags=["04a1b2"])
        self.store.put("papa", "Papa", "telegram", "-100987654321")

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_store_is_empty_and_is_not_created_by_reading(self):
        store = ContactStore(Path(self.directory.name) / "absent.json")

        self.assertEqual(store.load(), {})
        self.assertFalse(store.path.exists())

    def test_exact_recipient_resolves(self):
        self.assertEqual(self.store.get("oma").address, "123456789")
        self.assertEqual(self.store.get(" OMA ").id, "oma")

    def test_unknown_recipient_refuses_rather_than_defaulting(self):
        with self.assertRaises(VoicenoteError):
            self.store.get("tante")
        with self.assertRaises(VoicenoteError):
            self.store.get("")
        with self.assertRaises(VoicenoteError):
            self.store.get(None)

    def test_tag_resolves_to_its_recipient(self):
        self.assertEqual(self.store.resolve_tag("04A1B2").id, "oma")

    def test_unmapped_tag_refuses_rather_than_defaulting(self):
        with self.assertRaises(VoicenoteError):
            self.store.resolve_tag("deadbeef")

    def test_a_tag_can_only_belong_to_one_recipient(self):
        self.store.assign_tag("04a1b2", "papa")

        self.assertEqual(self.store.resolve_tag("04a1b2").id, "papa")
        self.assertEqual(self.store.load()["oma"].tags, ())

    def test_a_tag_on_two_recipients_refuses_instead_of_picking_one(self):
        # Only reachable by hand-editing the store; writes keep tags unique.
        self.store.path.write_text(
            '{"version": 1, "recipients": {'
            '"oma": {"label": "Oma", "transport": "telegram", "address": "1",'
            ' "tags": ["04a1b2"]},'
            '"papa": {"label": "Papa", "transport": "telegram", "address": "2",'
            ' "tags": ["04a1b2"]}}}'
        )

        with self.assertRaises(VoicenoteError):
            self.store.resolve_tag("04a1b2")

    def test_unassigning_a_tag_makes_it_refuse_again(self):
        self.store.unassign_tag("04a1b2")

        with self.assertRaises(VoicenoteError):
            self.store.resolve_tag("04a1b2")

    def test_inbound_address_matches_only_within_its_transport(self):
        self.assertEqual(self.store.resolve_address("telegram", "123456789").id, "oma")
        self.assertIsNone(self.store.resolve_address("signal", "123456789"))
        self.assertIsNone(self.store.resolve_address("telegram", "999"))

    def test_addresses_are_validated_per_transport(self):
        with self.assertRaises(VoicenoteError):
            self.store.put("x", "X", "telegram", "+31612345678")
        with self.assertRaises(VoicenoteError):
            self.store.put("x", "X", "signal", "12345")
        self.store.put("x", "X", "signal", "+31612345678")

    def test_unknown_transport_is_refused(self):
        with self.assertRaises(VoicenoteError):
            self.store.put("x", "X", "carrier_pigeon", "123")

    def test_re_saving_a_recipient_keeps_their_tag(self):
        # The ingress form has no tags field; correcting an address must not
        # quietly unpair the tag on the fridge.
        self.store.put("oma", "Oma", "telegram", "987654321")

        self.assertEqual(self.store.get("oma").tags, ("04a1b2",))
        self.assertEqual(self.store.resolve_tag("04a1b2").id, "oma")

    def test_tags_can_still_be_replaced_or_cleared_explicitly(self):
        self.store.put("oma", "Oma", "telegram", "1", tags=["0bcd"])
        self.assertEqual(self.store.get("oma").tags, ("0bcd",))

        self.store.put("oma", "Oma", "telegram", "1", tags=[])
        self.assertEqual(self.store.get("oma").tags, ())

    def test_a_stored_tag_in_another_case_is_still_treated_as_the_same_tag(self):
        self.store.path.write_text(
            '{"version": 1, "recipients": {'
            '"oma": {"label": "Oma", "transport": "telegram", "address": "1",'
            ' "tags": ["04A1B2"]},'
            '"papa": {"label": "Papa", "transport": "telegram", "address": "2",'
            ' "tags": []}}}'
        )

        self.store.assign_tag("04a1b2", "papa")

        self.assertEqual(self.store.resolve_tag("04a1b2").id, "papa")
        self.assertEqual(self.store.get("oma").tags, ())

    def test_a_tag_stored_in_another_case_can_be_unassigned(self):
        self.store.path.write_text(
            '{"version": 1, "recipients": {'
            '"oma": {"label": "Oma", "transport": "telegram", "address": "1",'
            ' "tags": ["04A1B2"]}}}'
        )

        self.store.unassign_tag("04a1b2")

        with self.assertRaises(VoicenoteError):
            self.store.resolve_tag("04a1b2")

    def test_malformed_store_refuses_to_load_instead_of_looking_empty(self):
        self.store.path.write_text('{"version": 1, "recipients": {"oma": {"label": 5}}}')

        with self.assertRaises(VoicenoteError):
            self.store.load()

    def test_malformed_store_is_never_overwritten_by_a_mutation(self):
        original = '{"version": 1, "recipients": {"oma": {"label": 5}}}'
        self.store.path.write_text(original)

        with self.assertRaises(VoicenoteError):
            self.store.put("papa", "Papa", "telegram", "1")

        self.assertEqual(self.store.path.read_text(), original)

    def test_removing_an_absent_recipient_reports_it(self):
        with self.assertRaises(VoicenoteError):
            self.store.remove("tante")

    def test_every_write_advances_the_revision(self):
        before = self.store.load()
        self.store.put("tante", "Tante", "telegram", "5")

        self.assertEqual(len(before) + 1, len(self.store.load()))


if __name__ == "__main__":
    unittest.main()
