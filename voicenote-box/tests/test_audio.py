import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from voicenote_box import audio
from voicenote_box.errors import VoicenoteError


SILENCE = b"\x00\x00" * 16_000


class Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class Runner:
    def __init__(self, result=None, error=None):
        self.result = result or Result()
        self.error = error
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        if self.result.returncode == 0:
            Path(command[-1]).write_bytes(b"OggS")
        return self.result


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "note.wav"

    def tearDown(self):
        self.directory.cleanup()

    def test_a_closed_recording_is_a_readable_wav(self):
        recording = audio.Recording(self.path, rate=16000, width=2, channels=1)
        recording.write(SILENCE)
        path = recording.close()

        with wave.open(str(path)) as handle:
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getnframes(), 16000)

    def test_duration_tracks_what_was_written(self):
        recording = audio.Recording(self.path)
        recording.write(SILENCE)
        recording.write(SILENCE)

        self.assertAlmostEqual(recording.seconds, 2.0, places=3)

    def test_an_in_progress_recording_is_not_visible_under_its_final_name(self):
        recording = audio.Recording(self.path)
        recording.write(SILENCE)

        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_name(self.path.name + ".part").exists())

        recording.close()
        self.assertTrue(self.path.exists())

    def test_a_discarded_recording_leaves_nothing_behind(self):
        recording = audio.Recording(self.path)
        recording.write(SILENCE)
        recording.discard()

        self.assertEqual(list(self.path.parent.glob("*")), [])

    def test_the_duration_of_a_written_file_can_be_read_back(self):
        recording = audio.Recording(self.path)
        recording.write(SILENCE)
        path = recording.close()

        self.assertAlmostEqual(audio.wav_seconds(path), 1.0, places=3)


class TranscodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "note.wav"
        self.source.write_bytes(b"RIFF")

    def tearDown(self):
        self.directory.cleanup()

    def test_outgoing_audio_becomes_mono_opus_in_an_ogg_container(self):
        runner = Runner()

        audio.to_opus(self.source, self.root / "note.ogg", run=runner)

        command = runner.commands[0]
        self.assertIn("libopus", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-f") + 1], "ogg")

    def test_the_result_only_appears_once_the_transcode_succeeded(self):
        runner = Runner()
        destination = self.root / "note.ogg"

        audio.to_opus(self.source, destination, run=runner)

        self.assertTrue(destination.exists())
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_incoming_audio_is_shaped_for_the_box_speaker(self):
        runner = Runner()

        audio.to_playable_wav(self.source, self.root / "reply.wav", run=runner)

        command = runner.commands[0]
        self.assertIn("-af", command)
        self.assertEqual(command[command.index("-f") + 1], "wav")

    def test_a_failed_transcode_reports_ffmpeg_and_leaves_no_output(self):
        runner = Runner(Result(returncode=1, stderr="Invalid data found"))
        destination = self.root / "note.ogg"

        with self.assertRaises(VoicenoteError) as caught:
            audio.to_opus(self.source, destination, run=runner)

        self.assertIn("Invalid data found", str(caught.exception))
        self.assertFalse(destination.exists())

    def test_a_missing_ffmpeg_is_reported_clearly(self):
        runner = Runner(error=FileNotFoundError())

        with self.assertRaises(VoicenoteError) as caught:
            audio.to_opus(self.source, self.root / "note.ogg", run=runner)

        self.assertIn("ffmpeg", str(caught.exception))

    def test_a_hanging_transcode_is_reported_rather_than_blocking_forever(self):
        runner = Runner(error=subprocess.TimeoutExpired("ffmpeg", 1))

        with self.assertRaises(VoicenoteError) as caught:
            audio.to_opus(self.source, self.root / "note.ogg", run=runner)

        self.assertIn("timed out", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
