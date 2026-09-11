"""Who the box currently sends to.

Set by an NFC tag scan or the recipient select entity, read when the button
is held. Unset is a real state, not a missing one: with nothing selected the
box refuses to record rather than picking someone.
"""

from __future__ import annotations

from pathlib import Path

from voicenote_box.contacts import ContactStore, Recipient
from voicenote_box.errors import VoicenoteError
from voicenote_box.paths import SELECTION_FILE
from voicenote_box.store import exclusive_lock, read_json, write_json


class Selection:
    def __init__(self, contacts: ContactStore, path: Path = SELECTION_FILE) -> None:
        self.contacts = contacts
        self.path = Path(path)

    def current(self) -> Recipient | None:
        """The selected recipient, or None if unset or no longer enrolled."""
        document = read_json(self.path)
        recipient_id = document.get("recipient_id") if isinstance(document, dict) else None
        if not recipient_id:
            return None
        try:
            return self.contacts.get(recipient_id)
        except VoicenoteError:
            # Enrolment was revoked after the tag was scanned. Treat the
            # selection as unset rather than routing to a removed contact.
            return None

    def select(self, recipient_id: str) -> Recipient:
        recipient = self.contacts.get(recipient_id)
        with exclusive_lock(self.path):
            write_json(self.path, {"recipient_id": recipient.id})
        return recipient

    def select_by_label(self, label: str) -> Recipient:
        wanted = " ".join(str(label).split()).casefold()
        for recipient in self.contacts.load().values():
            if recipient.label.casefold() == wanted:
                return self.select(recipient.id)
        raise VoicenoteError("no recipient with that name")

    def select_by_tag(self, tag_id: str) -> Recipient:
        return self.select(self.contacts.resolve_tag(tag_id).id)

    def clear(self) -> None:
        with exclusive_lock(self.path):
            write_json(self.path, {"recipient_id": None})
