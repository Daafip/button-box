"""The ingress page renders untrusted text safely.

Contact labels are typed by a caregiver and sender names and error strings
come from a messaging service. All three reach this page.
"""

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from voicenote_box import ui
from voicenote_box.activity import Activity
from voicenote_box.app import Application
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.mode import ModeStore
from voicenote_box.queue import MessageQueue
from voicenote_box.selection import Selection


INJECTION = "<script>alert(1)</script>"


class Balance(HTMLParser):
    VOID = {"meta", "br", "input", "base", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(tag)
        else:
            self.stack.pop()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.contacts = ContactStore(root / "contacts.json")
        self.queue = MessageQueue(root / "voicenote.db")
        self.selection = Selection(self.contacts, root / "selection.json")
        self.application = Application(
            Config(), self.contacts, self.selection, ModeStore(root / "mode.json"),
            self.queue, Activity(), key=b"0" * 32,
        )
        self.root = root

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def test_an_empty_box_renders_and_says_what_is_missing(self):
        page = ui.page(self.application)

        self.assertIn("Nog geen contacten", page)
        self.assertIn("weigert de box", page)

    def test_the_markup_stays_balanced_with_content(self):
        self.contacts.put("oma", "Oma", "telegram", "1")
        self.queue.enqueue_inbound("telegram", "1", "oma", "Oma", self.root / "a.wav", 7.0)
        self.queue.enqueue_outbound("oma", self.root / "x.ogg", 4.0)

        balance = Balance()
        balance.feed(ui.page(self.application))

        self.assertEqual(balance.stack, [])
        self.assertEqual(balance.problems, [])

    def test_a_contact_label_cannot_inject_markup(self):
        self.contacts.put("oma", f"Oma {INJECTION}", "telegram", "1")

        page = ui.page(self.application)

        self.assertNotIn(INJECTION, page)
        self.assertIn("&lt;script&gt;", page)

    def test_an_error_string_from_a_service_cannot_inject_markup(self):
        self.contacts.put("oma", "Oma", "telegram", "1")
        message_id = self.queue.enqueue_outbound("oma", self.root / "x.ogg", 4.0)
        self.queue.mark_failed(message_id, INJECTION)

        page = ui.page(self.application)

        self.assertNotIn(INJECTION, page)

    def test_a_sender_name_from_a_service_cannot_inject_markup(self):
        self.queue.enqueue_inbound(
            "telegram", "1", "oma", INJECTION, self.root / "a.wav", 3.0
        )

        self.assertNotIn(INJECTION, ui.page(self.application))

    def test_a_reported_error_cannot_inject_markup(self):
        self.assertNotIn(INJECTION, ui.page(self.application, error=INJECTION))

    def test_failed_notes_are_shown_with_the_way_to_clear_them(self):
        self.contacts.put("oma", "Oma", "telegram", "1")
        message_id = self.queue.enqueue_outbound("oma", self.root / "x.ogg", 4.0)
        self.queue.mark_failed(message_id, "chat not found")

        page = ui.page(self.application)

        self.assertIn("weigert de box nieuwe opnames", page)
        self.assertIn("ui/failed/clear", page)

    def test_forms_are_relative_to_the_ingress_path(self):
        page = ui.page(self.application, "/api/hassio_ingress/abc/")

        self.assertIn('<base href="/api/hassio_ingress/abc/">', page)
        self.assertIn('action="ui/recipients"', page)


if __name__ == "__main__":
    unittest.main()
