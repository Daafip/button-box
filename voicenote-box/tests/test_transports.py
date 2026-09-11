import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voicenote_box.config import Config
from voicenote_box.errors import TransportError
from voicenote_box.transports import build
from voicenote_box.transports.telegram import TelegramTransport
from voicenote_box.transports.whatsapp_cloud import (
    OUTSIDE_WINDOW_CODE,
    WhatsAppCloudTransport,
)


TOKEN = "123456:SECRET-BOT-TOKEN"


class TransportSelectionTests(unittest.TestCase):
    def test_an_unknown_transport_is_refused(self):
        with self.assertRaises(Exception):
            build(Config(transport="carrier_pigeon"))

    def test_telegram_without_a_token_refuses_to_start(self):
        with self.assertRaises(TransportError):
            build(Config(transport="telegram", telegram_token=""))


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.note = Path(self.directory.name) / "note.ogg"
        self.note.write_bytes(b"OggS")
        self.transport = TelegramTransport(
            Config(transport="telegram", telegram_token=TOKEN)
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_sending_posts_the_voice_file_to_the_chat(self):
        with mock.patch("voicenote_box.transports.telegram.post_multipart") as upload:
            upload.return_value = {"ok": True, "result": {"message_id": 42}}

            self.assertEqual(self.transport.send_voice("123456789", self.note), "42")

        url, fields, field_name, path = upload.call_args.args
        self.assertTrue(url.endswith("/sendVoice"))
        self.assertEqual(fields, {"chat_id": "123456789"})
        self.assertEqual(field_name, "voice")
        self.assertEqual(path, self.note)

    def test_a_refusal_from_telegram_is_permanent(self):
        with mock.patch("voicenote_box.transports.telegram.post_multipart") as upload:
            upload.return_value = {"ok": False, "description": "chat not found"}

            with self.assertRaises(TransportError) as caught:
                self.transport.send_voice("123456789", self.note)

        self.assertTrue(caught.exception.permanent)
        self.assertIn("chat not found", str(caught.exception))

    def test_the_bot_token_never_leaks_into_an_error(self):
        with mock.patch("voicenote_box.transports.telegram.post_multipart") as upload:
            upload.side_effect = TransportError(f"network error for bot{TOKEN}/sendVoice")

            with self.assertRaises(TransportError) as caught:
                self.transport.send_voice("123456789", self.note)

        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_polling_returns_voice_messages_and_the_next_cursor(self):
        payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 700,
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 123456789},
                        "from": {"first_name": "Oma"},
                        "voice": {"file_id": "AwAC", "duration": 6},
                    },
                }
            ],
        }
        with mock.patch("voicenote_box.transports.telegram.request_json",
                        return_value=payload):
            messages, cursor = self.transport.poll(None)

        self.assertEqual(cursor, "701")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender_address, "123456789")
        self.assertEqual(messages[0].sender_name, "Oma")
        self.assertEqual(messages[0].media_ref, "AwAC")
        self.assertEqual(messages[0].seconds, 6)

    def test_text_only_updates_advance_the_cursor_without_queueing(self):
        payload = {
            "ok": True,
            "result": [
                {"update_id": 800, "message": {"message_id": 1, "text": "hoi",
                                               "chat": {"id": 1}}}
            ],
        }
        with mock.patch("voicenote_box.transports.telegram.request_json",
                        return_value=payload):
            messages, cursor = self.transport.poll("800")

        self.assertEqual(messages, [])
        self.assertEqual(cursor, "801")

    def test_polling_resumes_from_the_stored_cursor(self):
        with mock.patch("voicenote_box.transports.telegram.request_json",
                        return_value={"ok": True, "result": []}) as get:
            self.transport.poll("901")

        self.assertIn("offset=901", get.call_args.args[0])


class WhatsAppCloudTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.note = Path(self.directory.name) / "note.ogg"
        self.note.write_bytes(b"OggS")
        self.transport = WhatsAppCloudTransport(
            Config(transport="whatsapp_cloud", whatsapp_token="T0KEN",
                   whatsapp_phone_number_id="55501")
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_a_closed_messaging_window_is_permanent_and_explained(self):
        with mock.patch("voicenote_box.transports.whatsapp_cloud.post_multipart",
                        return_value={"id": "media-1"}), \
             mock.patch("voicenote_box.transports.whatsapp_cloud.request_json") as send:
            send.side_effect = TransportError(
                json.dumps({"error": {"code": OUTSIDE_WINDOW_CODE}}), permanent=True
            )

            with self.assertRaises(TransportError) as caught:
                self.transport.send_voice("+31612345678", self.note)

        self.assertTrue(caught.exception.permanent)
        self.assertIn("24-hour", str(caught.exception))

    def test_sending_uploads_the_media_then_references_it(self):
        with mock.patch("voicenote_box.transports.whatsapp_cloud.post_multipart",
                        return_value={"id": "media-1"}), \
             mock.patch("voicenote_box.transports.whatsapp_cloud.request_json",
                        return_value={"messages": [{"id": "wamid.1"}]}) as send:
            self.assertEqual(self.transport.send_voice("+31612345678", self.note), "wamid.1")

        body = send.call_args.kwargs["body"]
        self.assertEqual(body["to"], "31612345678")
        self.assertEqual(body["audio"], {"id": "media-1"})

    def test_inbound_is_unsupported_without_a_public_webhook(self):
        self.assertEqual(self.transport.poll("x"), ([], "x"))


class WacliRunner:
    def __init__(self, stdout="{}", returncode=0, error=None):
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.commands = []

    def __call__(self, command, **keywords):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return type(
            "Result", (), {"returncode": self.returncode, "stdout": self.stdout,
                           "stderr": "oma@s.whatsapp.net refused"}
        )()


class WacliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.note = self.root / "note.ogg"
        self.note.write_bytes(b"OggS")
        self.config = Config(transport="wacli", wacli_binary="/usr/local/bin/wacli")

    def tearDown(self):
        self.directory.cleanup()

    def _transport(self, runner):
        from voicenote_box.transports.wacli import WacliTransport

        return WacliTransport(self.config, run=runner)

    def test_sending_shells_out_to_wacli_send_voice(self):
        runner = WacliRunner('{"success": true, "data": {"id": "3EB0"}}')

        sent = self._transport(runner).send_voice("31612345678@s.whatsapp.net", self.note)

        self.assertEqual(sent, "3EB0")
        command = runner.commands[0]
        self.assertEqual(command[:4], ["/usr/local/bin/wacli", "send", "voice", "--file"])
        self.assertIn("31612345678@s.whatsapp.net", command)
        self.assertIn("--json", command)

    def test_only_incoming_audio_is_reported(self):
        runner = WacliRunner(json.dumps({"data": {"messages": [
            {"MsgID": "A", "ChatJID": "1@s.whatsapp.net", "MediaType": "audio",
             "SenderName": "Oma"},
            {"MsgID": "B", "ChatJID": "1@s.whatsapp.net", "MediaType": "text"},
            {"MsgID": "C", "ChatJID": "1@s.whatsapp.net", "MediaType": "audio",
             "FromMe": True},
        ]}}))

        messages, _ = self._transport(runner).poll(None)

        self.assertEqual([message.remote_id for message in messages], ["A"])
        self.assertEqual(messages[0].sender_address, "1@s.whatsapp.net")

    def test_a_failure_never_repeats_what_wacli_said_about_whom(self):
        runner = WacliRunner(returncode=1)

        with self.assertRaises(TransportError) as caught:
            self._transport(runner).send_voice("1@s.whatsapp.net", self.note)

        self.assertNotIn("@s.whatsapp.net", str(caught.exception))

    def test_a_missing_binary_is_permanent(self):
        runner = WacliRunner(error=FileNotFoundError())

        with self.assertRaises(TransportError) as caught:
            self._transport(runner).send_voice("1@s.whatsapp.net", self.note)

        self.assertTrue(caught.exception.permanent)

    def test_whatsapp_jids_are_valid_addresses_and_phone_numbers_are_not(self):
        from voicenote_box.contacts import ContactStore

        store = ContactStore(self.root / "contacts.json")
        store.put("oma", "Oma", "wacli", "31612345678@s.whatsapp.net")
        store.put("thuis", "Thuis", "wacli", "120363123456789@g.us")

        with self.assertRaises(Exception):
            store.put("x", "X", "wacli", "+31612345678")


if __name__ == "__main__":
    unittest.main()
