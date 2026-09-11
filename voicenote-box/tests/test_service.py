"""The worker loops: they must keep running, and they must stop."""

import threading
import unittest

from voicenote_box.__main__ import SEND_INTERVAL_SECONDS, poller_loop, sender_loop


class FakeCourier:
    def __init__(self, error=None):
        self.sends = 0
        self.polls = 0
        self.error = error
        self.done = threading.Event()

    def send_due(self):
        self.sends += 1
        self.done.set()
        if self.error is not None:
            raise self.error

    def fetch_inbound(self):
        self.polls += 1
        self.done.set()
        if self.error is not None:
            raise self.error


class LoopTests(unittest.TestCase):
    def _run(self, target, *arguments):
        courier = arguments[0]
        stop = threading.Event()
        thread = threading.Thread(target=target, args=(*arguments, stop), daemon=True)
        thread.start()
        self.assertTrue(courier.done.wait(timeout=5))
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_sending_runs_on_its_own_short_timer(self):
        # A note must not wait behind a 25-second inbound long poll.
        self.assertLessEqual(SEND_INTERVAL_SECONDS, 5)

    def test_the_sender_runs_and_stops(self):
        courier = FakeCourier()

        self._run(sender_loop, courier)

        self.assertGreaterEqual(courier.sends, 1)
        self.assertEqual(courier.polls, 0)

    def test_the_poller_runs_and_stops(self):
        courier = FakeCourier()

        self._run(poller_loop, courier, 0.01)

        self.assertGreaterEqual(courier.polls, 1)
        self.assertEqual(courier.sends, 0)

    def test_a_raising_pass_is_logged_and_the_loop_survives(self):
        courier = FakeCourier(error=RuntimeError("transport exploded"))

        with self.assertLogs("voicenote_box", "ERROR"):
            self._run(sender_loop, courier)


class RecipientSelectTests(unittest.TestCase):
    """The select entity's fail-closed option must actually disarm the box."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from voicenote_box.activity import Activity
        from voicenote_box.app import Application
        from voicenote_box.config import Config
        from voicenote_box.contacts import ContactStore
        from voicenote_box.mode import ModeStore
        from voicenote_box.queue import MessageQueue
        from voicenote_box.selection import Selection

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        contacts = ContactStore(root / "contacts.json")
        contacts.put("oma", "Oma", "telegram", "1")
        self.queue = MessageQueue(root / "voicenote.db")
        self.application = Application(
            Config(), contacts, Selection(contacts, root / "selection.json"),
            ModeStore(root / "mode.json"), self.queue, Activity(), key=b"0" * 32,
        )

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def _choose(self, label):
        from voicenote_box.__main__ import recipient_chooser

        recipient_chooser(self.application)(label)

    def test_a_contact_name_selects_it(self):
        self._choose("Oma")

        self.assertEqual(self.application.state()["recipient"], "oma")

    def test_the_none_option_clears_the_selection(self):
        from voicenote_box.errors import VoicenoteError
        from voicenote_box.mqtt import NO_RECIPIENT

        self._choose("Oma")
        self._choose(NO_RECIPIENT)

        self.assertIsNone(self.application.state()["recipient"])
        with self.assertRaises(VoicenoteError):
            self.application.arm("voicenote")

    def test_an_unknown_name_is_refused_without_changing_the_selection(self):
        self._choose("Oma")

        with self.assertLogs("voicenote_box", "WARNING"):
            self._choose("Tante")

        self.assertEqual(self.application.state()["recipient"], "oma")


if __name__ == "__main__":
    unittest.main()
