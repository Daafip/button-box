import unittest

from voicenote_box.activity import Activity
from voicenote_box.mqtt import ERROR, IDLE, RECORDING, SENDING


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.published = []
        self.activity = Activity(self.published.append)

    def test_a_new_box_is_idle(self):
        self.assertEqual(self.activity.state, IDLE)

    def test_recording_outranks_sending(self):
        self.activity.set_sending(True)
        self.activity.set_recording(True)

        self.assertEqual(self.activity.state, RECORDING)

    def test_the_earlier_state_returns_when_recording_ends(self):
        self.activity.set_sending(True)
        self.activity.set_recording(True)
        self.activity.set_recording(False)

        self.assertEqual(self.activity.state, SENDING)

    def test_a_stuck_note_shows_once_the_box_is_otherwise_quiet(self):
        self.activity.set_error(True)

        self.assertEqual(self.activity.state, ERROR)

        self.activity.set_error(False)
        self.assertEqual(self.activity.state, IDLE)

    def test_only_changes_are_published(self):
        self.activity.set_recording(True)
        self.activity.set_recording(True)
        self.activity.set_recording(False)

        self.assertEqual(self.published, [RECORDING, IDLE])


if __name__ == "__main__":
    unittest.main()
