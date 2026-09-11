"""Fixed filesystem layout for the Voice Note Box add-on.

Home Assistant gives an add-on a persistent ``/data`` volume. Everything the
box must survive a restart with lives there. ``VOICENOTE_DATA_DIR`` exists so
tests and local runs can point the whole layout at a scratch directory.
"""

from __future__ import annotations

import os
from pathlib import Path


DATA_DIR = Path(os.environ.get("VOICENOTE_DATA_DIR", "/data"))

RECORDINGS_DIR = DATA_DIR / "recordings"
OUTBOX_DIR = DATA_DIR / "outbox"
INBOX_DIR = DATA_DIR / "inbox"
STATE_DIR = DATA_DIR / "state"

DB_FILE = DATA_DIR / "voicenote.db"
CONTACTS_FILE = DATA_DIR / "contacts.json"
MODE_FILE = STATE_DIR / "mode.json"
SELECTION_FILE = STATE_DIR / "selection.json"
