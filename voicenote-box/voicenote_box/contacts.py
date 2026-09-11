"""Fail-closed recipient allowlist.

Routing a voice note is only ever the result of an exact match: a recipient id
that exists, or an NFC tag that is assigned to one. There is deliberately no
default recipient and no fallback -- an unknown tag or an unset recipient
refuses the send rather than delivering a private message to the wrong person.

The same allowlist gates inbound audio: a message from an address nobody is
enrolled under is dropped instead of queued.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voicenote_box.errors import VoicenoteError
from voicenote_box.paths import CONTACTS_FILE
from voicenote_box.store import exclusive_lock, read_json, write_json


SCHEMA_VERSION = 1
TRANSPORTS = frozenset({"telegram", "signal", "wacli", "whatsapp_cloud"})
MAX_LABEL_LENGTH = 80

_RECIPIENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_TAG_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TELEGRAM_CHAT = re.compile(r"^-?[0-9]{1,20}$")
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
_SIGNAL_GROUP = re.compile(r"^group\.[A-Za-z0-9+/=_-]{1,128}$")
_WHATSAPP_JID = re.compile(r"^[0-9]{1,20}(?:-[0-9]{1,20})?@(?:s\.whatsapp\.net|g\.us)$")

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Recipient:
    id: str
    label: str
    transport: str
    address: str
    tags: tuple[str, ...] = ()


def _empty_document() -> JsonObject:
    return {"version": SCHEMA_VERSION, "revision": 0, "recipients": {}}


def _clean_recipient_id(value: object) -> str:
    if not isinstance(value, str) or not _RECIPIENT_ID.fullmatch(value.strip().casefold()):
        raise VoicenoteError("recipient id is invalid")
    return value.strip().casefold()


def _clean_tag_id(value: object) -> str:
    if not isinstance(value, str) or not _TAG_ID.fullmatch(value.strip().casefold()):
        raise VoicenoteError("tag id is invalid")
    return value.strip().casefold()


def _clean_label(value: object) -> str:
    if not isinstance(value, str):
        raise VoicenoteError("label is invalid")
    label = " ".join(value.split())
    if not label or len(label) > MAX_LABEL_LENGTH:
        raise VoicenoteError("label is invalid")
    return label


def _clean_transport(value: object) -> str:
    if not isinstance(value, str) or value.strip().casefold() not in TRANSPORTS:
        raise VoicenoteError("transport is invalid")
    return value.strip().casefold()


def _clean_address(transport: str, value: object) -> str:
    if not isinstance(value, str):
        raise VoicenoteError("address is invalid")
    address = value.strip()
    if transport == "telegram" and _TELEGRAM_CHAT.fullmatch(address):
        return address
    if transport == "signal" and (_E164.fullmatch(address) or _SIGNAL_GROUP.fullmatch(address)):
        return address
    if transport == "wacli" and _WHATSAPP_JID.fullmatch(address):
        return address
    if transport == "whatsapp_cloud" and _E164.fullmatch(address):
        return address
    raise VoicenoteError("address is invalid")


def _recipient(recipient_id: str, raw: object) -> Recipient:
    if not isinstance(raw, dict):
        raise VoicenoteError("recipient entry is invalid")
    transport = _clean_transport(raw.get("transport"))
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise VoicenoteError("tag list is invalid")
    return Recipient(
        id=recipient_id,
        label=_clean_label(raw.get("label")),
        transport=transport,
        address=_clean_address(transport, raw.get("address")),
        tags=tuple(sorted({_clean_tag_id(tag) for tag in tags})),
    )


class ContactStore:
    def __init__(self, path: Path = CONTACTS_FILE) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Recipient]:
        """Return every enrolled recipient, keyed by id.

        A malformed store raises: refusing to route is the safe outcome, and
        silently loading an empty allowlist would look like "nobody enrolled".
        """
        document = read_json(self.path, _empty_document())
        if not isinstance(document, dict):
            raise VoicenoteError("contact store is malformed")
        raw = document.get("recipients")
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise VoicenoteError("contact store is malformed")
        return {
            _clean_recipient_id(key): _recipient(_clean_recipient_id(key), value)
            for key, value in raw.items()
        }

    def get(self, recipient_id: object) -> Recipient:
        """Resolve an exact recipient id or refuse."""
        if not isinstance(recipient_id, str) or not recipient_id.strip():
            raise VoicenoteError("no recipient selected")
        wanted = recipient_id.strip().casefold()
        recipient = self.load().get(wanted)
        if recipient is None:
            raise VoicenoteError("recipient is not enrolled")
        return recipient

    def resolve_tag(self, tag_id: object) -> Recipient:
        """Resolve an NFC tag to its one recipient or refuse.

        Writes through this class keep a tag on a single recipient, but a
        hand-edited store can break that. An ambiguous tag refuses rather
        than picking whichever entry happens to come first.
        """
        wanted = _clean_tag_id(tag_id)
        matches = [
            recipient for recipient in self.load().values() if wanted in recipient.tags
        ]
        if not matches:
            raise VoicenoteError("tag is not assigned to a recipient")
        if len(matches) > 1:
            raise VoicenoteError("tag is assigned to more than one recipient")
        return matches[0]

    def resolve_address(self, transport: str, address: str) -> Recipient | None:
        """Match an inbound sender against the allowlist, or return None."""
        transport = transport.strip().casefold()
        address = address.strip()
        for recipient in self.load().values():
            if recipient.transport == transport and recipient.address == address:
                return recipient
        return None

    def put(
        self,
        recipient_id: str,
        label: str,
        transport: str,
        address: str,
        tags: list[str] | None = None,
    ) -> Recipient:
        """Create or replace a recipient, keeping tags unique across the store.

        ``tags=None`` keeps whatever tags the recipient already has, so
        correcting an address does not silently unpair the tag on the
        fridge. Pass an explicit list, or ``[]``, to change them.
        """
        recipient_id = _clean_recipient_id(recipient_id)
        with exclusive_lock(self.path):
            document = self._read_document()
            existing = document["recipients"].get(recipient_id) or {}
            kept = existing.get("tags", []) if tags is None else tags
            entry = {
                "label": _clean_label(label),
                "transport": _clean_transport(transport),
                "address": address,
                "tags": sorted({_clean_tag_id(tag) for tag in kept}),
            }
            entry["address"] = _clean_address(entry["transport"], address)
            document["recipients"][recipient_id] = entry
            self._write_document(document, owner=recipient_id)
            return _recipient(recipient_id, entry)

    def remove(self, recipient_id: str) -> None:
        recipient_id = _clean_recipient_id(recipient_id)
        with exclusive_lock(self.path):
            document = self._read_document()
            if document["recipients"].pop(recipient_id, None) is None:
                raise VoicenoteError("recipient is not enrolled")
            self._write_document(document)

    def assign_tag(self, tag_id: str, recipient_id: str) -> Recipient:
        """Bind a tag to one recipient, detaching it from any other."""
        tag_id = _clean_tag_id(tag_id)
        recipient_id = _clean_recipient_id(recipient_id)
        with exclusive_lock(self.path):
            document = self._read_document()
            entry = document["recipients"].get(recipient_id)
            if entry is None:
                raise VoicenoteError("recipient is not enrolled")
            entry["tags"] = sorted({*entry.get("tags", []), tag_id})
            self._write_document(document, owner=recipient_id)
            return _recipient(recipient_id, entry)

    def unassign_tag(self, tag_id: str) -> None:
        tag_id = _clean_tag_id(tag_id)
        with exclusive_lock(self.path):
            document = self._read_document()
            for entry in document["recipients"].values():
                entry["tags"] = [tag for tag in entry.get("tags", []) if tag != tag_id]
            self._write_document(document)

    def _read_document(self) -> JsonObject:
        document = read_json(self.path, _empty_document())
        if not isinstance(document, dict) or not isinstance(document.get("recipients"), dict):
            raise VoicenoteError("contact store is malformed")
        # Validate before mutating so a corrupt entry cannot be written back,
        # and normalise the tags: uniqueness is enforced by comparing them,
        # so a hand-edited "TAG1" must not slip past a stored "tag1".
        recipients = {}
        for key, value in document["recipients"].items():
            recipient = _recipient(_clean_recipient_id(key), value)
            recipients[recipient.id] = {**value, "tags": list(recipient.tags)}
        document["recipients"] = recipients
        return document

    def _write_document(self, document: JsonObject, owner: str | None = None) -> None:
        """Persist, enforcing that each tag belongs to exactly one recipient."""
        for recipient_id, entry in document["recipients"].items():
            if owner is not None and recipient_id != owner:
                entry["tags"] = [
                    tag
                    for tag in entry.get("tags", [])
                    if tag not in set(document["recipients"][owner].get("tags", []))
                ]
        document["version"] = SCHEMA_VERSION
        document["revision"] = int(document.get("revision") or 0) + 1
        write_json(self.path, document)
