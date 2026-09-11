"""Crash-safe JSON persistence shared by the mode flag and contact store.

Every write lands through a temporary file in the same directory followed by
``os.replace``, so a reader never observes a half-written document and a crash
mid-write leaves the previous document intact.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def exclusive_lock(path: Path):
    """Serialize read-modify-write cycles on ``path`` between processes.

    The lock is a sidecar file rather than the document itself: ``os.replace``
    swaps the document's inode, which would drop a lock held on it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_json(path: Path, default: Any = None) -> Any:
    """Return the parsed document, or ``default`` when it does not exist yet.

    A corrupt document raises rather than silently resetting to the default;
    losing a recipient allowlist to a parse error must never look like success.
    """
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read())
    except FileNotFoundError:
        return default


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(document, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        os.unlink(handle.name)
        raise
