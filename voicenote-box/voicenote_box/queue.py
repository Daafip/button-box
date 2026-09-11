"""Durable outbound and inbound message queues.

Both live in one sqlite database on the add-on's persistent volume, so a
Home Assistant restart or a power cut loses neither a note waiting to be sent
nor a reply waiting to be played.

Outbound rows are retried with exponential backoff until they succeed. A
transport that reports a *permanent* refusal fails the row immediately --
retrying a message to an unknown recipient only hides the misconfiguration.
"""

from __future__ import annotations

import functools
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from voicenote_box.paths import DB_FILE


PENDING = "pending"
SENT = "sent"
FAILED = "failed"

FIRST_BACKOFF_SECONDS = 30.0
MAX_BACKOFF_SECONDS = 900.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    recipient_id TEXT NOT NULL,
    path TEXT NOT NULL,
    seconds REAL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_error TEXT,
    remote_id TEXT,
    settled_at REAL
);
CREATE INDEX IF NOT EXISTS outbound_due ON outbound (state, next_attempt_at);

CREATE TABLE IF NOT EXISTS inbound (
    id INTEGER PRIMARY KEY,
    received_at REAL NOT NULL,
    transport TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_label TEXT NOT NULL,
    path TEXT NOT NULL,
    seconds REAL,
    read_at REAL,
    UNIQUE (transport, remote_id)
);
CREATE INDEX IF NOT EXISTS inbound_unread ON inbound (read_at, received_at);

CREATE TABLE IF NOT EXISTS cursors (
    transport TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _locked(method):
    """Serialize one database method.

    The proxy, both HTTP servers and the courier all touch the queue from
    different threads. They share one connection behind this lock rather
    than one connection each, so shutdown can close it deterministically.
    """

    @functools.wraps(method)
    def guarded(self, *arguments, **keywords):
        with self._lock:
            return method(self, *arguments, **keywords)

    return guarded


@dataclass(frozen=True)
class Outbound:
    id: int
    created_at: float
    recipient_id: str
    path: Path
    seconds: float | None
    attempts: int
    last_error: str | None


@dataclass(frozen=True)
class Inbound:
    id: int
    received_at: float
    transport: str
    sender_id: str
    sender_label: str
    path: Path
    seconds: float | None
    read_at: float | None


def backoff_seconds(attempts: int) -> float:
    """Delay before retry number ``attempts`` (1-based), capped."""
    return min(FIRST_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)


class MessageQueue:
    def __init__(self, path: Path = DB_FILE, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        # A queued note must survive a power cut, not just a clean shutdown.
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- outbound ---------------------------------------------------------

    @_locked
    def enqueue_outbound(self, recipient_id: str, path: Path, seconds: float | None = None) -> int:
        now = self._clock()
        cursor = self._db.execute(
            "INSERT INTO outbound (created_at, recipient_id, path, seconds, state,"
            " next_attempt_at) VALUES (?, ?, ?, ?, ?, ?)",
            (now, recipient_id, str(path), seconds, PENDING, now),
        )
        return int(cursor.lastrowid)

    @_locked
    def due_outbound(self, limit: int = 10) -> list[Outbound]:
        rows = self._db.execute(
            "SELECT * FROM outbound WHERE state = ? AND next_attempt_at <= ?"
            " ORDER BY created_at LIMIT ?",
            (PENDING, self._clock(), limit),
        ).fetchall()
        return [
            Outbound(
                id=row["id"],
                created_at=row["created_at"],
                recipient_id=row["recipient_id"],
                path=Path(row["path"]),
                seconds=row["seconds"],
                attempts=row["attempts"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    @_locked
    def mark_sent(self, message_id: int, remote_id: str | None = None) -> None:
        now = self._clock()
        self._db.execute(
            "UPDATE outbound SET state = ?, remote_id = ?, settled_at = ?, last_error = NULL,"
            " attempts = attempts + 1 WHERE id = ?",
            (SENT, remote_id, now, message_id),
        )

    @_locked
    def mark_retry(self, message_id: int, error: str) -> float:
        """Record a failed attempt and schedule the next one."""
        row = self._db.execute(
            "SELECT attempts FROM outbound WHERE id = ?", (message_id,)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        next_attempt_at = self._clock() + backoff_seconds(attempts)
        self._db.execute(
            "UPDATE outbound SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            (attempts, next_attempt_at, error[:500], message_id),
        )
        return next_attempt_at

    @_locked
    def mark_failed(self, message_id: int, error: str) -> None:
        self._db.execute(
            "UPDATE outbound SET state = ?, last_error = ?, settled_at = ?,"
            " attempts = attempts + 1 WHERE id = ?",
            (FAILED, error[:500], self._clock(), message_id),
        )

    @_locked
    def outbound_since(self, cutoff: float) -> int:
        """Count notes queued since ``cutoff``. Backs the send rate limit."""
        return self._count("SELECT COUNT(*) FROM outbound WHERE created_at >= ?", (cutoff,))

    @_locked
    def outbound_by_id(self, message_id: int) -> Outbound | None:
        row = self._db.execute("SELECT * FROM outbound WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            return None
        return Outbound(
            id=row["id"],
            created_at=row["created_at"],
            recipient_id=row["recipient_id"],
            path=Path(row["path"]),
            seconds=row["seconds"],
            attempts=row["attempts"],
            last_error=row["last_error"],
        )

    @_locked
    def outbound_pending(self) -> int:
        return self._count("SELECT COUNT(*) FROM outbound WHERE state = ?", (PENDING,))

    @_locked
    def outbound_failed(self) -> int:
        return self._count("SELECT COUNT(*) FROM outbound WHERE state = ?", (FAILED,))

    # -- inbound ----------------------------------------------------------

    @_locked
    def enqueue_inbound(
        self,
        transport: str,
        remote_id: str,
        sender_id: str,
        sender_label: str,
        path: Path,
        seconds: float | None = None,
    ) -> int | None:
        """Queue a received note. Returns None if this message is already known.

        Pollers re-see the same message after a crash or a cursor rewind, and
        the box must not play a reply twice.
        """
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO inbound (received_at, transport, remote_id, sender_id,"
            " sender_label, path, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._clock(), transport, remote_id, sender_id, sender_label, str(path), seconds),
        )
        return int(cursor.lastrowid) if cursor.rowcount else None

    @_locked
    def unread_count(self) -> int:
        return self._count("SELECT COUNT(*) FROM inbound WHERE read_at IS NULL")

    @_locked
    def next_unread(self) -> Inbound | None:
        row = self._db.execute(
            "SELECT * FROM inbound WHERE read_at IS NULL ORDER BY received_at, id LIMIT 1"
        ).fetchone()
        return self._inbound(row)

    @_locked
    def last_sender(self) -> str | None:
        row = self._db.execute(
            "SELECT sender_label FROM inbound ORDER BY received_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row["sender_label"] if row else None

    @_locked
    def mark_read(self, message_id: int) -> None:
        self._db.execute(
            "UPDATE inbound SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (self._clock(), message_id),
        )

    @_locked
    def recent_outbound(self, limit: int = 20) -> list[Outbound]:
        rows = self._db.execute(
            "SELECT id FROM outbound ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.outbound_by_id(row["id"]) for row in rows]

    @_locked
    def recent_inbound(self, limit: int = 20) -> list[Inbound]:
        rows = self._db.execute(
            "SELECT * FROM inbound ORDER BY received_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._inbound(row) for row in rows]

    @_locked
    def inbound_by_id(self, message_id: int) -> Inbound | None:
        row = self._db.execute("SELECT * FROM inbound WHERE id = ?", (message_id,)).fetchone()
        return self._inbound(row)

    # -- cursors ----------------------------------------------------------

    @_locked
    def cursor(self, transport: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM cursors WHERE transport = ?", (transport,)
        ).fetchone()
        return row["value"] if row else None

    @_locked
    def set_cursor(self, transport: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO cursors (transport, value) VALUES (?, ?)"
            " ON CONFLICT (transport) DO UPDATE SET value = excluded.value",
            (transport, str(value)),
        )

    @_locked
    def discard_failed(self) -> int:
        """Delete permanently failed notes and their audio. Returns the count."""
        rows = self._db.execute(
            "SELECT id, path FROM outbound WHERE state = ?", (FAILED,)
        ).fetchall()
        for row in rows:
            Path(row["path"]).unlink(missing_ok=True)
            self._db.execute("DELETE FROM outbound WHERE id = ?", (row["id"],))
        return len(rows)

    # -- retention --------------------------------------------------------

    @_locked
    def purge_settled(self, older_than_seconds: float) -> int:
        """Delete finished messages and their audio. Returns rows removed."""
        cutoff = self._clock() - older_than_seconds
        removed = 0
        for table, settled_column in (("outbound", "settled_at"), ("inbound", "read_at")):
            rows = self._db.execute(
                f"SELECT id, path FROM {table} WHERE {settled_column} IS NOT NULL"
                f" AND {settled_column} < ?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                Path(row["path"]).unlink(missing_ok=True)
                self._db.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
                removed += 1
        return removed

    # -- helpers ----------------------------------------------------------

    def _count(self, query: str, parameters: tuple = ()) -> int:
        return int(self._db.execute(query, parameters).fetchone()[0])

    @staticmethod
    def _inbound(row: sqlite3.Row | None) -> Inbound | None:
        if row is None:
            return None
        return Inbound(
            id=row["id"],
            received_at=row["received_at"],
            transport=row["transport"],
            sender_id=row["sender_id"],
            sender_label=row["sender_label"],
            path=Path(row["path"]),
            seconds=row["seconds"],
            read_at=row["read_at"],
        )
