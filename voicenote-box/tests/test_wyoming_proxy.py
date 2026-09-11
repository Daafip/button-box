import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.mode import VOICENOTE, ModeStore
from voicenote_box.queue import MessageQueue
from voicenote_box.sink import NoteSink
from voicenote_box.wyoming_protocol import (
    AUDIO_CHUNK,
    AUDIO_START,
    AUDIO_STOP,
    DESCRIBE,
    INFO,
    TRANSCRIBE,
    TRANSCRIPT,
    Event,
    read_event,
    write_event,
)
from voicenote_box.wyoming_proxy import AsrProxy


CHUNK = b"\x00\x00" * 16_000  # one second of 16 kHz mono PCM
AUDIO = {"rate": 16000, "width": 2, "channels": 1}


async def _close(writer):
    writer.close()
    await writer.wait_closed()


class FakeWhisper:
    """A Wyoming ASR service that answers with a fixed transcript."""

    def __init__(self, text="zet de lamp aan"):
        self.text = text
        self.chunks_before_stop = 0
        self.received = []
        self._stopped = False
        # Some recognisers answer as soon as they are confident, without
        # waiting for audio-stop. Set this to reproduce that ordering.
        self.answer_after_chunks = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            await self._serve(reader, writer)
        finally:
            await _close(writer)

    async def _serve(self, reader, writer):
        while True:
            event = await read_event(reader)
            if event is None:
                return
            self.received.append(event.type)
            if event.type == AUDIO_CHUNK and not self._stopped:
                self.chunks_before_stop += 1
                if self.chunks_before_stop == self.answer_after_chunks:
                    await write_event(writer, Event(TRANSCRIPT, {"text": self.text}))
            elif event.type == DESCRIBE:
                await write_event(writer, Event(INFO, {"asr": [{"name": "whisper"}]}))
            elif event.type == AUDIO_STOP:
                self._stopped = True
                await write_event(writer, Event(TRANSCRIPT, {"text": self.text}))


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.recordings = root / "recordings"
        self.whisper = FakeWhisper()
        port = await self.whisper.start()
        self.config = Config(upstream_stt_host="127.0.0.1", upstream_stt_port=port)
        self.queue = MessageQueue(root / "voicenote.db")
        self.contacts = ContactStore(root / "contacts.json")
        self.contacts.put("oma", "Oma", "telegram", "123456789")
        self.modes = ModeStore(root / "mode.json")
        self.states = []
        sink = NoteSink(
            self.config,
            self.queue,
            self.contacts,
            recordings_dir=self.recordings,
            outbox_dir=root / "outbox",
            transcode=self._transcode,
        )
        self.proxy = AsrProxy(self.config, sink, self.modes, status=self.states.append)
        self.server = await asyncio.start_server(self.proxy.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        self.whisper.server.close()
        self.queue.close()
        self.directory.cleanup()

    def _transcode(self, source, destination):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"OggS")
        return Path(destination)

    async def _utterance(self, seconds=2):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            await write_event(writer, Event(TRANSCRIBE, {"language": "nl"}))
            await write_event(writer, Event(AUDIO_START, AUDIO))
            for _ in range(seconds):
                await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
            await write_event(writer, Event(AUDIO_STOP, {}))
            return await asyncio.wait_for(read_event(reader), timeout=10)
        finally:
            writer.close()
            await writer.wait_closed()

    # -- the hard requirement: normal assist is unchanged -----------------

    async def test_a_spoken_command_gets_the_real_transcript_back(self):
        event = await self._utterance()

        self.assertEqual(event.type, TRANSCRIPT)
        self.assertEqual(event.data["text"], "zet de lamp aan")

    async def test_a_spoken_command_queues_nothing(self):
        await self._utterance()

        self.assertEqual(self.queue.outbound_pending(), 0)
        self.assertEqual(self.states, [])

    async def test_audio_reaches_the_recogniser_before_the_utterance_ends(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.addAsyncCleanup(_close, writer)
        await write_event(writer, Event(AUDIO_START, AUDIO))
        for _ in range(3):
            await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
        await asyncio.sleep(0.2)

        # Streaming, not buffering: the recogniser has the audio already.
        self.assertEqual(self.whisper.chunks_before_stop, 3)

        await write_event(writer, Event(AUDIO_STOP, {}))
        await asyncio.wait_for(read_event(reader), timeout=10)

    async def test_the_service_description_comes_from_the_real_recogniser(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.addAsyncCleanup(_close, writer)

        await write_event(writer, Event(DESCRIBE, {}))
        event = await asyncio.wait_for(read_event(reader), timeout=10)

        self.assertEqual(event.type, INFO)
        self.assertIn("whisper", event.data["asr"][0]["name"])
        self.assertIn(self.config.device_name, event.data["asr"][0]["name"])

    # -- voice note mode --------------------------------------------------

    async def test_an_armed_flag_queues_the_note_and_confirms_by_voice(self):
        self.modes.set(VOICENOTE, "oma")

        event = await self._utterance(seconds=3)

        self.assertEqual(event.data["text"], self.config.sent_sentence)
        due = self.queue.due_outbound()
        self.assertEqual([item.recipient_id for item in due], ["oma"])

    async def test_the_note_is_written_to_disk_tagged_as_a_voice_note(self):
        self.modes.set(VOICENOTE, "oma")
        self.config.assist_recording_retention_seconds = 600

        await self._utterance(seconds=3)

        self.assertEqual(len(list((Path(self.directory.name) / "outbox").glob("*.ogg"))), 1)

    async def test_the_flag_is_spent_so_the_next_utterance_is_a_command_again(self):
        self.modes.set(VOICENOTE, "oma")
        await self._utterance(seconds=3)

        event = await self._utterance()

        self.assertEqual(event.data["text"], "zet de lamp aan")
        self.assertEqual(self.queue.outbound_pending(), 1)

    async def test_recording_is_reported_while_a_note_is_being_captured(self):
        self.modes.set(VOICENOTE, "oma")

        await self._utterance(seconds=3)

        self.assertEqual(self.states, ["recording", "idle"])

    async def test_a_note_for_a_removed_recipient_says_so_instead_of_sending(self):
        self.modes.set(VOICENOTE, "oma")
        self.contacts.remove("oma")

        with self.assertLogs("voicenote_box.sink", "WARNING"):
            event = await self._utterance(seconds=3)

        self.assertEqual(event.data["text"], self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)

    async def test_a_transcript_arriving_early_still_becomes_the_confirmation(self):
        # The spoken words must never reach Assist as a command just because
        # the recogniser answered before the button was released.
        self.whisper.answer_after_chunks = 1
        self.modes.set(VOICENOTE, "oma")

        event = await self._utterance(seconds=3)

        self.assertEqual(event.data["text"], self.config.sent_sentence)
        self.assertEqual([item.recipient_id for item in self.queue.due_outbound()], ["oma"])

    async def test_an_early_transcript_for_a_command_is_still_passed_through(self):
        self.whisper.answer_after_chunks = 1

        event = await self._utterance(seconds=3)

        self.assertEqual(event.data["text"], "zet de lamp aan")

    async def test_a_failure_while_queueing_speaks_the_refusal(self):
        def explode(source, destination):
            raise OSError("no space left on device")

        self.proxy.sink._transcode = explode
        self.modes.set(VOICENOTE, "oma")

        with self.assertLogs("voicenote_box.sink", "ERROR"):
            event = await self._utterance(seconds=3)

        self.assertEqual(event.data["text"], self.config.failed_sentence)
        self.assertEqual(self.queue.outbound_pending(), 0)
        # The half-made note is cleaned up rather than left on the volume.
        self.assertEqual(list(self.recordings.glob("*")), [])

    async def test_two_utterances_on_one_connection_keep_their_own_answers(self):
        # The note is armed first and takes longer to prepare than the
        # command that follows it. Each answer must still come back on its
        # own utterance -- otherwise the note's real words reach Assist.
        def slow_transcode(source, destination):
            time.sleep(0.3)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(b"OggS")
            return Path(destination)

        self.proxy.sink._transcode = slow_transcode
        self.modes.set(VOICENOTE, "oma")
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.addAsyncCleanup(_close, writer)

        async def utterance(seconds):
            await write_event(writer, Event(AUDIO_START, AUDIO))
            for _ in range(seconds):
                await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
            await write_event(writer, Event(AUDIO_STOP, {}))

        await utterance(3)   # the voice note: armed, and slow to prepare
        await utterance(1)   # an ordinary command: the flag is already spent

        first = await asyncio.wait_for(read_event(reader), timeout=10)
        second = await asyncio.wait_for(read_event(reader), timeout=10)

        self.assertEqual(first.data["text"], self.config.sent_sentence)
        self.assertEqual(second.data["text"], "zet de lamp aan")
        self.assertEqual([item.recipient_id for item in self.queue.due_outbound()], ["oma"])

    async def test_a_restarted_utterance_leaves_no_orphan_recording(self):
        # audio-start twice with no stop between: the first recording can
        # never be finished and must not be left open on the volume.
        self.config.assist_recording_retention_seconds = 600
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.addAsyncCleanup(_close, writer)

        await write_event(writer, Event(AUDIO_START, AUDIO))
        await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
        await write_event(writer, Event(AUDIO_START, AUDIO))
        await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
        await write_event(writer, Event(AUDIO_STOP, {}))
        await asyncio.wait_for(read_event(reader), timeout=10)

        self.assertEqual(list(self.recordings.glob("*.part")), [])
        self.assertEqual(len(list(self.recordings.glob("*.wav"))), 1)

    # -- failure handling -------------------------------------------------

    async def test_a_dropped_connection_leaves_no_half_written_recording(self):
        self.modes.set(VOICENOTE, "oma")
        self.config.assist_recording_retention_seconds = 600
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        await write_event(writer, Event(AUDIO_START, AUDIO))
        await write_event(writer, Event(AUDIO_CHUNK, AUDIO, CHUNK))
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.2)

        self.assertEqual(list(self.recordings.glob("*")), [])

    async def test_an_unreachable_recogniser_does_not_crash_the_proxy(self):
        self.config.upstream_stt_port = 1  # nothing listens here

        with self.assertLogs("voicenote_box.wyoming_proxy", "ERROR"):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            self.addAsyncCleanup(_close, writer)
            self.assertIsNone(await asyncio.wait_for(read_event(reader), timeout=10))


if __name__ == "__main__":
    unittest.main()
