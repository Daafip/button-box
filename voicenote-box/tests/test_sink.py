import tempfile
import unittest
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.mode import ASSIST_MODE, Mode
from voicenote_box.queue import MessageQueue
from voicenote_box.sink import NoteSink


SILENCE = b"\x00\x00" * 16_000  # one second of 16 kHz mono PCM


class Clock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value


class NoteSinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.recordings = root / "recordings"
        self.outbox = root / "outbox"
        self.clock = Clock()
        self.config = Config(max_recording_seconds=5, rate_limit_per_hour=3)
        self.queue = MessageQueue(root / "voicenote.db", self.clock)
        self.contacts = ContactStore(root / "contacts.json")
        self.contacts.put("oma", "Oma", "telegram", "123456789")
        self.transcoded = []
        self.sink = NoteSink(
            self.config,
            self.queue,
            self.contacts,
            recordings_dir=self.recordings,
            outbox_dir=self.outbox,
            clock=self.clock,
            transcode=self._transcode,
        )
        self.voicenote = Mode("voicenote", "oma")

    def tearDown(self):
        self.queue.close()
        self.directory.cleanup()

    def _transcode(self, source, destination):
        self.transcoded.append((Path(source), Path(destination)))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"OggS")
        return Path(destination)

    def _record(self, mode, seconds=2):
        recording = self.sink.start(mode, 16000, 2, 1)
        for _ in range(seconds):
            self.sink.write(recording, SILENCE)
        return recording

    # -- assist -----------------------------------------------------------

    def test_assist_keeps_the_real_transcript_and_queues_nothing(self):
        outcome = self.sink.complete(self._record(ASSIST_MODE), ASSIST_MODE)

        self.assertIsNone(outcome.transcript)
        self.assertEqual(self.queue.outbound_pending(), 0)

    def test_assist_audio_is_discarded_by_default(self):
        self.sink.complete(self._record(ASSIST_MODE), ASSIST_MODE)

        self.assertEqual(list(self.recordings.glob("*.wav")), [])

    def test_assist_audio_is_kept_when_retention_is_configured(self):
        self.config.assist_recording_retention_seconds = 600

        self.sink.complete(self._record(ASSIST_MODE), ASSIST_MODE)

        kept = list(self.recordings.glob("*.wav"))
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].name.endswith("-assist.wav"))

    # -- voice notes ------------------------------------------------------

    def test_a_voice_note_is_transcoded_queued_and_confirmed(self):
        outcome = self.sink.complete(self._record(self.voicenote, 3), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.sent_sentence)
        due = self.queue.due_outbound()
        self.assertEqual([item.recipient_id for item in due], ["oma"])
        self.assertEqual(due[0].path.suffix, ".ogg")
        self.assertAlmostEqual(due[0].seconds, 3.0, places=2)

    def test_the_recording_is_tagged_with_its_mode_on_disk(self):
        recording = self._record(self.voicenote)

        self.assertTrue(recording.path.name.endswith("-voicenote.wav"))

    def test_the_wav_is_removed_once_it_has_been_transcoded(self):
        self.sink.complete(self._record(self.voicenote, 3), self.voicenote)

        self.assertEqual(list(self.recordings.glob("*.wav")), [])
        self.assertEqual(len(self.transcoded), 1)

    def test_recording_stops_at_the_configured_maximum(self):
        recording = self._record(self.voicenote, seconds=20)

        self.assertLessEqual(recording.seconds, self.config.max_recording_seconds + 1)

    # -- refusals ---------------------------------------------------------

    def test_a_removed_recipient_refuses_the_note_instead_of_rerouting(self):
        self.contacts.remove("oma")

        with self.assertLogs("voicenote_box.sink", "WARNING"):
            outcome = self.sink.complete(self._record(self.voicenote, 3), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)
        self.assertEqual(list(self.recordings.glob("*.wav")), [])

    def test_a_stab_at_the_button_is_too_short_to_send(self):
        recording = self.sink.start(self.voicenote, 16000, 2, 1)
        self.sink.write(recording, SILENCE[:1000])

        with self.assertLogs("voicenote_box.sink", "WARNING"):
            outcome = self.sink.complete(recording, self.voicenote)

        self.assertEqual(outcome.transcript, self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)

    def test_the_rate_limit_refuses_rather_than_silently_dropping(self):
        for _ in range(self.config.rate_limit_per_hour):
            self.sink.complete(self._record(self.voicenote, 2), self.voicenote)

        with self.assertLogs("voicenote_box.sink", "WARNING"):
            outcome = self.sink.complete(self._record(self.voicenote, 2), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), self.config.rate_limit_per_hour)

    def test_the_rate_limit_window_rolls_forward(self):
        for _ in range(self.config.rate_limit_per_hour):
            self.sink.complete(self._record(self.voicenote, 2), self.voicenote)
        self.clock.value += 3601

        outcome = self.sink.complete(self._record(self.voicenote, 2), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.sent_sentence)

    def test_a_degraded_outbox_refuses_new_notes_until_it_is_cleared(self):
        stuck = self.queue.enqueue_outbound("oma", self.outbox / "stuck.ogg")
        self.queue.mark_failed(stuck, "chat not found")

        with self.assertLogs("voicenote_box.sink", "WARNING"):
            outcome = self.sink.complete(self._record(self.voicenote, 2), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)

        self.assertEqual(self.queue.discard_failed(), 1)
        self.assertEqual(
            self.sink.complete(self._record(self.voicenote, 2), self.voicenote).transcript,
            self.config.sent_sentence,
        )

    def test_a_degraded_outbox_does_not_disturb_ordinary_commands(self):
        stuck = self.queue.enqueue_outbound("oma", self.outbox / "stuck.ogg")
        self.queue.mark_failed(stuck, "chat not found")

        outcome = self.sink.complete(self._record(ASSIST_MODE), ASSIST_MODE)

        self.assertIsNone(outcome.transcript)

    def test_an_unexpected_failure_still_refuses_and_cleans_up(self):
        def explode(source, destination):
            raise OSError("no space left on device")

        self.sink._transcode = explode

        with self.assertLogs("voicenote_box.sink", "ERROR"):
            outcome = self.sink.complete(self._record(self.voicenote, 3), self.voicenote)

        self.assertEqual(outcome.transcript, self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)
        self.assertEqual(list(self.recordings.glob("*")), [])

    def test_an_abandoned_recording_leaves_nothing_behind(self):
        self.sink.abandon(self._record(self.voicenote, 2))

        self.assertEqual(list(self.recordings.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
