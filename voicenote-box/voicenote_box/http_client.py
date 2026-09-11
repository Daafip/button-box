"""Small HTTP helper for the transports.

Only what the messaging APIs need: JSON requests, multipart uploads, and file
downloads, over ``urllib``. Credentials travel in URLs and headers here, so
nothing in this module logs a request -- callers log the outcome instead.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from voicenote_box.errors import TransportError


DEFAULT_TIMEOUT = 60

# Statuses where retrying the same request cannot change the answer.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 413, 422})


def _open(request: urllib.request.Request, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = (error.read() or b"")[:400].decode("utf-8", "replace")
        raise TransportError(
            f"HTTP {error.code}: {body}", permanent=error.code in PERMANENT_STATUSES
        ) from error
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise TransportError(f"network error: {error}") from error


def request_json(
    url: str,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    raw = _open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout)
    try:
        return json.loads(raw or b"{}")
    except ValueError as error:
        raise TransportError("response was not JSON") from error


def post_multipart(
    url: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Upload one file alongside plain form fields."""
    file_path = Path(file_path)
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\";"
        f" filename=\"{file_path.name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    request_headers = {
        **(headers or {}),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    raw = _open(
        urllib.request.Request(url, data=body, headers=request_headers, method="POST"), timeout
    )
    try:
        return json.loads(raw or b"{}")
    except ValueError as error:
        raise TransportError("upload response was not JSON") from error


def download(url: str, destination: Path, headers: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT) -> Path:
    """Fetch to a ``.part`` file and rename, so a partial file is never used."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    raw = _open(urllib.request.Request(url, headers=dict(headers or {})), timeout)
    partial.write_bytes(raw)
    partial.replace(destination)
    return destination
