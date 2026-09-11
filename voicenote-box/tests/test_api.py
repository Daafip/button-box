import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from voicenote_box import api
from voicenote_box.activity import Activity
from voicenote_box.app import Application
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.mode import VOICENOTE, ModeStore
from voicenote_box.queue import MessageQueue
from voicenote_box.selection import Selection


TOKEN = "test-token"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments):
        return None


class FakeHomeAssistant:
    def __init__(self):
        self.announced = []
        self.error = None

    def announce(self, entity_id, media_url):
        if self.error is not None:
            raise self.error
        self.announced.append((entity_id, media_url))


class ApiTestCase(unittest.TestCase):
    trusted = False

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config = Config(
            api_token=TOKEN,
            announce_entity="assist_satellite.box",
            media_base_url="http://box:8099",
        )
        self.queue = MessageQueue(self.root / "voicenote.db")
        self.contacts = ContactStore(self.root / "contacts.json")
        self.contacts.put("oma", "Oma", "telegram", "123456789", tags=["04a1b2"])
        self.modes = ModeStore(self.root / "mode.json")
        self.hass = FakeHomeAssistant()
        self.application = Application(
            self.config,
            self.contacts,
            Selection(self.contacts, self.root / "selection.json"),
            self.modes,
            self.queue,
            Activity(),
            hass=self.hass,
            key=b"0" * 32,
        )
        self.server = api.Server(("127.0.0.1", 0), self.application, self.trusted)
        self.port = self.server.server_address[1]
        # A short poll interval keeps shutdown() from costing half a second
        # per test; the add-on keeps the default.
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.queue.close()
        self.directory.cleanup()

    def request(self, path, method="GET", body=None, token=TOKEN, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()


class ControlApiTests(ApiTestCase):
    def test_health_needs_no_token(self):
        status, body = self.request("/health", token=None)

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_commands_without_a_token_are_refused(self):
        status, _ = self.request("/api/mode", "POST", {"mode": "voicenote"}, token=None)

        self.assertEqual(status, 401)
        self.assertFalse(self.modes.peek().is_voicenote)

    def test_a_wrong_token_is_refused(self):
        status, _ = self.request("/api/state", token="guess")

        self.assertEqual(status, 401)

    def test_arming_with_an_explicit_recipient_sets_the_flag(self):
        status, body = self.request(
            "/api/mode", "POST", {"mode": "voicenote", "recipient": "oma"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["recipient"], "oma")
        self.assertEqual(self.modes.peek().recipient_id, "oma")

    def test_arming_uses_the_current_selection_when_none_is_given(self):
        self.request("/api/recipient", "POST", {"tag": "04A1B2"})

        status, _ = self.request("/api/mode", "POST", {"mode": "voicenote"})

        self.assertEqual(status, 200)
        self.assertEqual(self.modes.peek().recipient_id, "oma")

    def test_arming_with_nothing_selected_refuses_to_record(self):
        status, body = self.request("/api/mode", "POST", {"mode": "voicenote"})

        self.assertEqual(status, 400)
        self.assertIn("recipient", json.loads(body)["error"])
        self.assertFalse(self.modes.peek().is_voicenote)

    def test_arming_for_an_unknown_recipient_refuses(self):
        status, _ = self.request(
            "/api/mode", "POST", {"mode": "voicenote", "recipient": "tante"}
        )

        self.assertEqual(status, 400)
        self.assertFalse(self.modes.peek().is_voicenote)

    def test_an_unknown_tag_refuses_rather_than_selecting_anyone(self):
        status, _ = self.request("/api/recipient", "POST", {"tag": "deadbeef"})

        self.assertEqual(status, 400)
        self.assertIsNone(json.loads(self.request("/api/state")[1])["recipient"])

    def test_releasing_back_to_assist_clears_the_flag(self):
        self.request("/api/mode", "POST", {"mode": "voicenote", "recipient": "oma"})

        self.request("/api/mode", "POST", {"mode": "assist"})

        self.assertFalse(self.modes.peek().is_voicenote)

    def test_the_ingress_page_is_not_served_on_the_published_port(self):
        status, _ = self.request("/", token=None)

        self.assertEqual(status, 404)

    def test_form_encoded_bodies_are_accepted(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/mode",
            data=b"mode=voicenote&recipient=oma",
            method="POST",
        )
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Authorization", f"Bearer {TOKEN}")

        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

        self.assertEqual(self.modes.peek().recipient_id, "oma")


class PlaybackTests(ApiTestCase):
    def _receive(self, remote_id="1"):
        path = self.root / f"{remote_id}.wav"
        path.write_bytes(b"RIFF" + b"\x00" * 40)
        return self.queue.enqueue_inbound(
            "telegram", remote_id, "oma", "Oma", path, 3.0
        )

    def test_playing_the_next_note_announces_it_and_clears_the_counter(self):
        message_id = self._receive()

        status, body = self.request("/api/play_next", "POST", {})

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], message_id)
        self.assertEqual(len(self.hass.announced), 1)
        self.assertEqual(self.queue.unread_count(), 0)

    def test_playing_with_an_empty_queue_is_not_an_error(self):
        status, body = self.request("/api/play_next", "POST", {})

        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["played"])

    def test_a_failed_announcement_keeps_the_note_unread(self):
        from voicenote_box.errors import TransportError

        self._receive()
        self.hass.error = TransportError("satellite offline")

        status, _ = self.request("/api/play_next", "POST", {})

        self.assertEqual(status, 400)
        self.assertEqual(self.queue.unread_count(), 1)

    def test_media_is_served_to_an_unauthenticated_player_with_a_valid_signature(self):
        message_id = self._receive()
        signature = self.application.media_signature("inbox", message_id)

        status, body = self.request(
            f"/media/inbox/{message_id}?t={signature}", token=None
        )

        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))

    def test_media_without_a_valid_signature_is_refused(self):
        message_id = self._receive()

        self.assertEqual(self.request(f"/media/inbox/{message_id}?t=wrong", token=None)[0], 403)
        self.assertEqual(self.request(f"/media/inbox/{message_id}", token=None)[0], 403)

    def test_a_signature_is_bound_to_its_own_message(self):
        first = self._receive("1")
        second = self._receive("2")

        status, _ = self.request(
            f"/media/inbox/{second}?t={self.application.media_signature('inbox', first)}",
            token=None,
        )

        self.assertEqual(status, 403)

    def test_a_ranged_request_returns_only_that_slice(self):
        message_id = self._receive()
        signature = self.application.media_signature("inbox", message_id)

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/media/inbox/{message_id}?t={signature}"
        )
        request.add_header("Range", "bytes=0-3")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"RIFF")

    def test_range_edge_cases_never_produce_a_malformed_response(self):
        message_id = self._receive()
        signature = self.application.media_signature("inbox", message_id)
        url = f"http://127.0.0.1:{self.port}/media/inbox/{message_id}?t={signature}"

        for header, expected in [
            ("bytes=0-3", 206),
            ("bytes=-4", 206),
            ("bytes=-", 200),        # not a range at all
            ("cheese", 200),         # unparseable
            ("bytes=-0", 416),       # a zero-length suffix
            ("bytes=1000-", 416),    # past the end of the file
            ("bytes=5-2", 416),      # inverted
        ]:
            with self.subTest(header=header):
                request = urllib.request.Request(url)
                request.add_header("Range", header)
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        status, body = response.status, response.read()
                        length = int(response.headers["Content-Length"])
                except urllib.error.HTTPError as error:
                    status, body = error.code, error.read()
                    length = int(error.headers["Content-Length"])
                self.assertEqual(status, expected)
                self.assertEqual(len(body), length)

    def test_a_zero_byte_recording_is_served_whole_not_as_a_partial(self):
        path = self.root / "empty.wav"
        path.write_bytes(b"")
        message_id = self.queue.enqueue_inbound(
            "telegram", "empty", "oma", "Oma", path, 0.0
        )
        signature = self.application.media_signature("inbox", message_id)

        status, body = self.request(f"/media/inbox/{message_id}?t={signature}", token=None)

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_marking_a_note_read_needs_the_token(self):
        message_id = self._receive()

        self.assertEqual(self.request(f"/api/messages/{message_id}/read", "POST",
                                      {}, token=None)[0], 401)
        self.assertEqual(self.request(f"/api/messages/{message_id}/read", "POST", {})[0], 200)
        self.assertEqual(self.queue.unread_count(), 0)


class IngressTests(ApiTestCase):
    trusted = True

    def test_the_page_renders_without_a_token(self):
        status, body = self.request("/", token=None)

        self.assertEqual(status, 200)
        self.assertIn(b"Voice Note Box", body)
        self.assertIn(b"Oma", body)

    def test_the_page_uses_the_ingress_prefix_for_its_forms(self):
        status, body = self.request(
            "/", token=None, headers={"X-Ingress-Path": "/api/hassio_ingress/abc"}
        )

        self.assertEqual(status, 200)
        self.assertIn(b'<base href="/api/hassio_ingress/abc/">', body)

    def post_form(self, path, body):
        """POST without following the redirect, so its target is visible."""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Ingress-Path", "/api/hassio_ingress/abc")
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers)

    def test_a_contact_can_be_added_and_the_browser_returns_to_the_page(self):
        status, headers = self.post_form(
            "/ui/recipients",
            {"id": "papa", "label": "Papa", "transport": "telegram", "address": "555"},
        )

        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/api/hassio_ingress/abc/?saved=1")
        self.assertEqual(self.contacts.get("papa").label, "Papa")

    def test_an_invalid_contact_reports_the_reason_instead_of_saving(self):
        status, headers = self.post_form(
            "/ui/recipients",
            {"id": "papa", "label": "Papa", "transport": "telegram", "address": "not-a-chat"},
        )

        self.assertEqual(status, 303)
        self.assertIn("error=", headers["Location"])
        self.assertEqual(list(self.contacts.load()), ["oma"])

    def test_the_reported_error_is_rendered_on_the_page(self):
        status, body = self.request(
            "/?error=address%20is%20invalid", token=None
        )

        self.assertEqual(status, 200)
        self.assertIn(b"address is invalid", body)

    def test_a_broken_contact_store_still_renders_the_page(self):
        # The page is where an operator goes to repair this, so it must not
        # answer a broken store by dropping the connection.
        self.contacts.path.write_text('{"version": 1, "recipients": {"oma": {"label": 5}}}')

        status, body = self.request("/", token=None)

        self.assertEqual(status, 200)
        self.assertIn("kan niet worden gelezen".encode(), body)

    def test_a_nonsense_content_length_is_refused_not_dropped(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/mode", data=b"", method="POST"
        )
        request.add_header("Content-Length", "0")
        request.add_header("Content-Type", "application/json")

        # An empty body means no mode, which is a refusal rather than a crash.
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code

        self.assertEqual(status, 400)

    def test_commands_are_trusted_without_a_token_behind_ingress(self):
        status, _ = self.request("/api/mode", "POST",
                                 {"mode": VOICENOTE, "recipient": "oma"}, token=None)

        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
