import tempfile
import unittest
from pathlib import Path

from voicenote_box.queue import MAX_BACKOFF_SECONDS, MessageQueue, backoff_seconds


class Clock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value


class OutboundQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "voicenote.db"
        self.clock = Clock()
        self.queue = MessageQueue(self.path, self.clock)

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def test_a_queued_note_is_due_immediately(self):
        message_id = self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"), 4.0)

        due = self.queue.due_outbound()

        self.assertEqual([item.id for item in due], [message_id])
        self.assertEqual(due[0].recipient_id, "oma")

    def test_a_retry_is_not_due_until_its_backoff_has_passed(self):
        message_id = self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"))

        self.queue.mark_retry(message_id, "network error")

        self.assertEqual(self.queue.due_outbound(), [])
        self.clock.value += backoff_seconds(1)
        self.assertEqual([item.id for item in self.queue.due_outbound()], [message_id])

    def test_backoff_grows_and_is_capped(self):
        self.assertLess(backoff_seconds(1), backoff_seconds(3))
        self.assertEqual(backoff_seconds(99), MAX_BACKOFF_SECONDS)

    def test_a_sent_note_leaves_the_queue(self):
        message_id = self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"))

        self.queue.mark_sent(message_id, "42")

        self.assertEqual(self.queue.due_outbound(), [])
        self.assertEqual(self.queue.outbound_pending(), 0)

    def test_a_permanently_failed_note_stops_retrying_and_is_visible(self):
        message_id = self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"))

        self.queue.mark_failed(message_id, "recipient is not enrolled")

        self.clock.value += 10_000
        self.assertEqual(self.queue.due_outbound(), [])
        self.assertEqual(self.queue.outbound_failed(), 1)

    def test_pending_notes_survive_a_restart(self):
        self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"))
        self.queue.close()

        reopened = MessageQueue(self.path, self.clock)
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.outbound_pending(), 1)

    def test_rate_limit_counts_only_the_recent_window(self):
        self.queue.enqueue_outbound("oma", Path("/tmp/a.ogg"))
        self.clock.value += 7200
        self.queue.enqueue_outbound("oma", Path("/tmp/b.ogg"))

        self.assertEqual(self.queue.outbound_since(self.clock.value - 3600), 1)


class InboundQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "voicenote.db"
        self.clock = Clock()
        self.queue = MessageQueue(self.path, self.clock)

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def _receive(self, remote_id="1", label="Oma"):
        return self.queue.enqueue_inbound(
            "telegram", remote_id, "oma", label, Path(f"/tmp/{remote_id}.wav"), 3.0
        )

    def test_a_received_note_counts_as_unread(self):
        self._receive()

        self.assertEqual(self.queue.unread_count(), 1)
        self.assertEqual(self.queue.next_unread().sender_label, "Oma")
        self.assertEqual(self.queue.last_sender(), "Oma")

    def test_the_same_message_is_never_queued_twice(self):
        self.assertIsNotNone(self._receive("7"))
        self.assertIsNone(self._receive("7"))
        self.assertEqual(self.queue.unread_count(), 1)

    def test_reading_decrements_the_counter_oldest_first(self):
        first = self._receive("1")
        self.clock.value += 10
        self._receive("2")

        self.assertEqual(self.queue.next_unread().id, first)
        self.queue.mark_read(first)

        self.assertEqual(self.queue.unread_count(), 1)
        self.assertNotEqual(self.queue.next_unread().id, first)

    def test_unread_notes_survive_a_restart(self):
        self._receive("1")
        self._receive("2")
        self.queue.mark_read(self.queue.next_unread().id)
        self.queue.close()

        reopened = MessageQueue(self.path, self.clock)
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.unread_count(), 1)

    def test_a_cursor_round_trips_and_survives_a_restart(self):
        self.queue.set_cursor("telegram", "1001")
        self.queue.set_cursor("telegram", "1002")
        self.queue.close()

        reopened = MessageQueue(self.path, self.clock)
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.cursor("telegram"), "1002")
        self.assertIsNone(reopened.cursor("signal"))


class RetentionTests(unittest.TestCase):
    def test_purge_removes_settled_rows_and_their_audio_but_keeps_unread(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            clock = Clock()
            queue = MessageQueue(directory / "voicenote.db", clock)
            self.addCleanup(queue.close)
            played = directory / "played.wav"
            waiting = directory / "waiting.wav"
            for path in (played, waiting):
                path.write_bytes(b"RIFF")

            read_id = queue.enqueue_inbound("telegram", "1", "oma", "Oma", played, 1.0)
            queue.enqueue_inbound("telegram", "2", "oma", "Oma", waiting, 1.0)
            queue.mark_read(read_id)
            clock.value += 86_400 * 30

            self.assertEqual(queue.purge_settled(86_400 * 14), 1)
            self.assertFalse(played.exists())
            self.assertTrue(waiting.exists())
            self.assertEqual(queue.unread_count(), 1)


if __name__ == "__main__":
    unittest.main()
