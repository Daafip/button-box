import tempfile
import unittest
from pathlib import Path

from voicenote_box.activity import Activity
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.courier import MAX_MESSAGE_ATTEMPTS, Courier
from voicenote_box.errors import TransportError
from voicenote_box.mqtt import ERROR, IDLE
from voicenote_box.queue import MessageQueue
from voicenote_box.transports.base import IncomingMessage


class Clock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeTransport:
    name = "telegram"

    def __init__(self):
        self.sent = []
        self.send_error = None
        self.inbox = []
        self.download_error = None
        self.polled = []

    def send_voice(self, address, ogg_path):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((address, Path(ogg_path)))
        return f"remote-{len(self.sent)}"

    def poll(self, cursor):
        self.polled.append(cursor)
        messages, self.inbox = self.inbox, []
        return messages, (messages[-1].cursor if messages else cursor)

    def download(self, message, destination):
        if self.download_error is not None:
            raise self.download_error
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"OggS")
        return Path(destination)


class CourierTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.clock = Clock()
        self.queue = MessageQueue(self.root / "voicenote.db", self.clock)
        self.contacts = ContactStore(self.root / "contacts.json")
        self.contacts.put("oma", "Oma", "telegram", "123456789")
        self.transport = FakeTransport()
        self.activity = Activity()
        self.courier = Courier(
            Config(),
            self.queue,
            self.contacts,
            self.transport,
            self.activity,
            inbox_dir=self.root / "inbox",
            clock=self.clock,
            transcode=self._transcode,
        )

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def _transcode(self, source, destination):
        Path(destination).write_bytes(b"RIFF")
        return Path(destination)

    def _note(self, name="a.ogg"):
        path = self.root / name
        path.write_bytes(b"OggS")
        return self.queue.enqueue_outbound("oma", path, 3.0)


class SendingTests(CourierTestCase):
    def test_a_queued_note_is_delivered_to_the_enrolled_address(self):
        self._note()

        self.assertEqual(self.courier.send_due(), 1)
        self.assertEqual(self.transport.sent[0][0], "123456789")
        self.assertEqual(self.queue.outbound_pending(), 0)

    def test_a_network_failure_retries_instead_of_losing_the_note(self):
        self._note()
        self.transport.send_error = TransportError("network error")

        with self.assertLogs("voicenote_box.courier", "WARNING"):
            self.courier.send_due()

        self.assertEqual(self.queue.outbound_pending(), 1)
        self.assertEqual(self.queue.due_outbound(), [])

        self.transport.send_error = None
        self.clock.value += 3600
        self.assertEqual(self.courier.send_due(), 1)

    def test_a_permanent_refusal_stops_retrying_and_shows_up_as_failed(self):
        self._note()
        self.transport.send_error = TransportError("HTTP 403: blocked", permanent=True)

        with self.assertLogs("voicenote_box.courier", "ERROR"):
            self.courier.send_due()

        self.clock.value += 100_000
        self.assertEqual(self.queue.due_outbound(), [])
        self.assertEqual(self.queue.outbound_failed(), 1)

    def test_a_note_for_a_removed_recipient_fails_instead_of_rerouting(self):
        self._note()
        self.contacts.remove("oma")
        self.contacts.put("tante", "Tante", "telegram", "555")

        with self.assertLogs("voicenote_box.courier", "ERROR"):
            self.courier.send_due()

        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.queue.outbound_failed(), 1)

    def test_a_note_whose_audio_vanished_fails_rather_than_sending_nothing(self):
        message_id = self._note()
        self.queue.due_outbound()[0].path.unlink()

        with self.assertLogs("voicenote_box.courier", "ERROR"):
            self.courier.send_due()

        self.assertEqual(self.transport.sent, [])
        self.assertIsNotNone(message_id)
        self.assertEqual(self.queue.outbound_failed(), 1)

    def test_a_stuck_note_lights_the_error_state_and_clears_when_drained(self):
        self._note()
        self.transport.send_error = TransportError("nope", permanent=True)
        with self.assertLogs("voicenote_box.courier", "ERROR"):
            self.courier.send_due()

        self.assertEqual(self.activity.state, ERROR)


class ReceivingTests(CourierTestCase):
    def _incoming(self, remote_id="1", address="123456789", cursor="101", seconds=4.0):
        return IncomingMessage(
            remote_id=remote_id,
            sender_address=address,
            sender_name="Oma",
            media_ref=f"file-{remote_id}",
            cursor=cursor,
            seconds=seconds,
        )

    def test_a_reply_from_a_contact_is_queued_as_unread(self):
        self.transport.inbox = [self._incoming()]

        self.assertEqual(self.courier.fetch_inbound(), 1)
        message = self.queue.next_unread()
        self.assertEqual(message.sender_label, "Oma")
        self.assertEqual(message.path.suffix, ".wav")

    def test_audio_from_a_stranger_is_dropped_without_logging_the_address(self):
        self.transport.inbox = [self._incoming(address="999999")]

        with self.assertLogs("voicenote_box.courier", "INFO") as captured:
            self.assertEqual(self.courier.fetch_inbound(), 0)

        self.assertEqual(self.queue.unread_count(), 0)
        self.assertNotIn("999999", "".join(captured.output))

    def test_the_same_reply_is_not_queued_twice_after_a_replay(self):
        self.transport.inbox = [self._incoming()]
        self.courier.fetch_inbound()
        self.transport.inbox = [self._incoming()]

        with self.assertLogs("voicenote_box.courier", "INFO"):
            self.courier.fetch_inbound()

        self.assertEqual(self.queue.unread_count(), 1)

    def test_the_cursor_only_advances_past_messages_that_were_stored(self):
        self.transport.inbox = [self._incoming(cursor="101")]
        self.courier.fetch_inbound()

        self.assertEqual(self.queue.cursor("telegram"), "101")

    def test_a_download_failure_rewinds_the_cursor_so_the_reply_is_retried(self):
        self.transport.inbox = [self._incoming(cursor="101")]
        self.transport.download_error = TransportError("network error")

        with self.assertLogs("voicenote_box.courier", "WARNING"):
            self.assertEqual(self.courier.fetch_inbound(), 0)

        self.assertEqual(self.queue.cursor("telegram"), "100")
        self.assertEqual(self.queue.unread_count(), 0)

    def test_one_undownloadable_message_cannot_wedge_the_queue_forever(self):
        self.transport.download_error = TransportError("gone")

        for _ in range(MAX_MESSAGE_ATTEMPTS):
            self.transport.inbox = [self._incoming(cursor="101")]
            with self.assertLogs("voicenote_box.courier", "WARNING"):
                self.courier.fetch_inbound()

        self.assertEqual(self.queue.cursor("telegram"), "101")

    def test_a_cursorless_transport_keeps_the_rest_of_the_batch(self):
        # signal-cli deletes envelopes as it hands them over, so there is
        # nothing to rewind to. Abandoning the batch would lose the others
        # as well as the one that failed.
        self.transport.inbox = [
            self._incoming("1", cursor=None),
            self._incoming("2", cursor=None),
            self._incoming("3", cursor=None),
        ]
        original = self.courier._accept
        self.courier._accept = lambda message: (
            "retry" if message.remote_id == "1" else original(message)
        )

        with self.assertLogs("voicenote_box.courier", "ERROR"):
            self.assertEqual(self.courier.fetch_inbound(), 2)

        self.assertEqual(self.queue.unread_count(), 2)

    def test_a_cursorless_failure_is_not_remembered_forever(self):
        self.transport.inbox = [self._incoming("1", cursor=None)]
        self.transport.download_error = TransportError("gone")

        with self.assertLogs("voicenote_box.courier", "WARNING"):
            self.courier.fetch_inbound()

        # The message can never come back, so no attempt counter is kept.
        self.assertEqual(dict(self.courier._attempts), {})

    def test_an_unreadable_duration_does_not_abort_the_poll(self):
        # wave raises wave.Error and EOFError, neither of them an OSError.
        def broken_transcode(source, destination):
            Path(destination).write_bytes(b"not a wav at all")
            return Path(destination)

        self.courier._transcode = broken_transcode
        self.transport.inbox = [self._incoming("1", seconds=None)]

        self.assertEqual(self.courier.fetch_inbound(), 1)
        self.assertIsNone(self.queue.next_unread().seconds)

    def test_a_failed_poll_reports_an_error_rather_than_raising(self):
        def explode(cursor):
            raise TransportError("network error")

        self.transport.poll = explode

        with self.assertLogs("voicenote_box.courier", "WARNING"):
            self.assertEqual(self.courier.fetch_inbound(), 0)

        self.assertEqual(self.activity.state, ERROR)

    def test_a_quiet_poll_leaves_the_box_idle(self):
        self.assertEqual(self.courier.fetch_inbound(), 0)

        self.assertEqual(self.activity.state, IDLE)


if __name__ == "__main__":
    unittest.main()
