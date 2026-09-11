"""HTTP front ends: the control API and the ingress UI.

Two servers with one set of routes:

* the **control** server is published on the host, so every command needs the
  bearer token from the add-on options;
* the **ingress** server is only reachable through Home Assistant, which has
  already authenticated the user, so it serves the UI and trusts its callers.

Media is the exception. A satellite fetching a WAV cannot send a header, so
media URLs carry an HMAC of the message id instead and need no token.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from voicenote_box import ui
from voicenote_box.app import Application
from voicenote_box.errors import VoicenoteError


log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024

# Distinguishes "this range cannot be served" from "send the whole file".
UNSATISFIABLE = object()
MEDIA_ROUTE = re.compile(r"^/media/(inbox|outbox)/([0-9]+)$")
READ_ROUTE = re.compile(r"^/api/messages/([0-9]+)/read$")
DELETE_RECIPIENT_ROUTE = re.compile(r"^/ui/recipients/([a-z0-9_-]{1,32})/delete$")
CONTENT_TYPES = {".wav": "audio/wav", ".ogg": "audio/ogg"}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, application: Application, trusted: bool) -> None:
        self.application = application
        self.trusted = trusted
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "voicenote-box"
    protocol_version = "HTTP/1.1"

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server's interface
        path, query = self._split()
        media = MEDIA_ROUTE.match(path)
        if media:
            self._serve_media(media.group(1), int(media.group(2)), query.get("t", [""])[0])
        elif path == "/health":
            self._json({"ok": True})
        elif path == "/api/state":
            self._guarded(lambda: self._json(self.app.state()))
        elif path in ("/", "/index.html") and self.server.trusted:
            self._guarded(lambda: self._page(query), authorize=False)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - http.server's interface
        path, _ = self._split()
        body = self._body()
        read = READ_ROUTE.match(path)
        if path == "/api/mode":
            self._guarded(lambda: self._json(
                self.app.arm(str(body.get("mode", "")), body.get("recipient") or None)
            ))
        elif path == "/api/recipient":
            self._guarded(lambda: self._json(self._select(body)))
        elif path == "/api/play_next":
            self._guarded(lambda: self._json(self.app.play_next() or {"played": None}))
        elif read:
            self._guarded(lambda: self._json_after(self.app.mark_read, int(read.group(1))))
        elif self.server.trusted and path.startswith("/ui/"):
            self._ui_command(path, body)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    # -- handlers ---------------------------------------------------------

    def _select(self, body: dict) -> dict:
        recipient = self.app.select(
            recipient_id=body.get("recipient") or None,
            tag=body.get("tag") or None,
            label=body.get("label") or None,
        )
        return {"recipient": recipient.id if recipient else None,
                "label": recipient.label if recipient else None}

    def _page(self, query: dict) -> None:
        self._html(ui.page(
            self.app,
            self.headers.get("X-Ingress-Path", ""),
            notice="Opgeslagen." if query.get("saved") else "",
            error=query.get("error", [""])[0],
        ))

    def _ui_command(self, path: str, body: dict) -> None:
        removal = DELETE_RECIPIENT_ROUTE.match(path)
        try:
            if path == "/ui/recipients":
                self.app.contacts.put(
                    str(body.get("id", "")),
                    str(body.get("label", "")),
                    str(body.get("transport", self.app.config.transport)),
                    str(body.get("address", "")),
                )
            elif removal:
                self.app.contacts.remove(removal.group(1))
            elif path == "/ui/tags":
                self.app.contacts.assign_tag(str(body.get("tag", "")),
                                             str(body.get("recipient", "")))
            elif path == "/ui/failed/clear":
                self.app.queue.discard_failed()
                self.app.activity.set_error(False)
            elif path == "/ui/select":
                self.app.select(recipient_id=body.get("recipient") or None)
            else:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            self.app.republish_entities()
        except VoicenoteError as error:
            return self._redirect(f"?error={urllib.parse.quote(str(error))}")
        self._redirect("?saved=1")

    def _serve_media(self, kind: str, message_id: int, signature: str) -> None:
        if not self.app.media_signature_valid(kind, message_id, signature):
            return self._error(HTTPStatus.FORBIDDEN, "bad signature")
        message = (
            self.app.queue.inbound_by_id(message_id) if kind == "inbox"
            else self.app.queue.outbound_by_id(message_id)
        )
        if message is None or not Path(message.path).exists():
            return self._error(HTTPStatus.NOT_FOUND, "no such recording")
        self._send_file(Path(message.path))

    def _send_file(self, path: Path) -> None:
        payload = path.read_bytes()
        content_type = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        wanted = self._requested_range(len(payload))
        if wanted is UNSATISFIABLE:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{len(payload)}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        chunk = payload if wanted is None else payload[wanted[0]:wanted[1] + 1]
        self.send_response(HTTPStatus.OK if wanted is None else HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if wanted is not None:
            self.send_header(
                "Content-Range", f"bytes {wanted[0]}-{wanted[1]}/{len(payload)}"
            )
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def _requested_range(self, size: int):
        """Parse a single byte range.

        Returns None for "send the whole file" -- which covers no Range
        header and any header this does not understand -- a (first, last)
        pair, or UNSATISFIABLE for a range that lies outside the file.
        """
        header = (self.headers.get("Range") or "").strip()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header) if header else None
        if not match:
            return None
        first, last = match.groups()
        if not first and not last:
            return None  # "bytes=-" is not a range
        if size == 0:
            return UNSATISFIABLE
        if not first:
            length = int(last)
            if length == 0:
                return UNSATISFIABLE
            return max(size - length, 0), size - 1
        start = int(first)
        if start >= size:
            return UNSATISFIABLE
        end = min(int(last), size - 1) if last else size - 1
        if end < start:
            return UNSATISFIABLE
        return start, end

    # -- plumbing ---------------------------------------------------------

    @property
    def app(self) -> Application:
        return self.server.application

    def _split(self) -> tuple[str, dict]:
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path.rstrip("/") or "/", urllib.parse.parse_qs(parsed.query)

    def _authorized(self) -> bool:
        if self.server.trusted:
            return True
        expected = self.app.config.api_token
        if not expected:
            return False
        offered = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return offered.startswith(prefix) and hmac.compare_digest(
            offered[len(prefix):], expected
        )

    def _guarded(self, action, authorize: bool = True) -> None:
        """Run a handler, turning any failure into a response.

        Without this a raising handler drops the connection with no reply,
        which is worst on the ingress page: that is where an operator goes
        to repair whatever is broken.
        """
        if authorize and not self._authorized():
            return self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
        try:
            action()
        except VoicenoteError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except Exception:
            log.exception("request failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _json_after(self, action, *arguments) -> None:
        action(*arguments)
        self._json({"ok": True})

    def _body(self) -> dict:
        try:
            declared = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        length = min(max(declared, 0), MAX_BODY_BYTES)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        if "json" in (self.headers.get("Content-Type") or ""):
            try:
                parsed = json.loads(raw)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {
            key: value[0]
            for key, value in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()
        }

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._respond(status, "application/json", json.dumps(payload).encode("utf-8"))

    def _html(self, markup: str) -> None:
        self._respond(HTTPStatus.OK, "text/html; charset=utf-8", markup.encode("utf-8"))

    def _redirect(self, query: str) -> None:
        """Send the browser back to the page, not to the POST-only path.

        Behind ingress the page lives at ``X-Ingress-Path``; the form action
        is relative to it, so the redirect has to name it explicitly.
        """
        root = (self.headers.get("X-Ingress-Path") or "").rstrip("/") + "/"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"{root}{query}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _respond(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        # The default logger prints the full request line; media URLs carry a
        # signature in the query string, so only the path is recorded.
        log.debug("%s %s", self.command, urllib.parse.urlsplit(self.path).path)


def serve(application: Application, port: int, trusted: bool) -> Server:
    server = Server(("0.0.0.0", port), application, trusted)
    log.info("http %s server on :%s", "ingress" if trusted else "control", port)
    return server
