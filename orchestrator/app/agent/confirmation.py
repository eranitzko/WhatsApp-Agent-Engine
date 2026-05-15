"""Pending confirmation state for destructive or external actions.

Flow:
  1. Agent calls request_confirmation(action, params, description).
  2. System stores a PendingAction for the group with a 5-min TTL.
  3. Next admin message:
       - "yes" / "כן" / "confirm" / "אישור" → execute the stored action.
       - anything else (including a new image) → cancel and process normally.
  4. On cancel or expiry, a cancellation note is returned.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

TTL_MINUTES = 5

CONFIRM_WORDS = {"yes", "כן", "confirm", "אישור", "ok", "approve"}
CANCEL_WORDS  = {"no", "לא", "cancel", "ביטול", "abort"}


@dataclass
class PendingAction:
    action: str                     # e.g. "remove_invoice", "send_email"
    params: dict[str, Any]          # arguments needed to execute
    description: str                # human-readable summary shown to admin
    expires: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=TTL_MINUTES))

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires


class ConfirmationStore:
    """One pending action per group at a time."""

    def __init__(self) -> None:
        self._store: dict[str, PendingAction] = {}

    def set(self, group_id: str, action: str, params: dict, description: str) -> None:
        self._store[group_id] = PendingAction(action=action, params=params, description=description)

    def get(self, group_id: str) -> PendingAction | None:
        pending = self._store.get(group_id)
        if pending and pending.is_expired():
            del self._store[group_id]
            return None
        return pending

    def clear(self, group_id: str) -> None:
        self._store.pop(group_id, None)

    def is_confirm(self, text: str) -> bool:
        return text.strip().lower() in CONFIRM_WORDS

    def is_cancel(self, text: str) -> bool:
        return text.strip().lower() in CANCEL_WORDS


# Singleton
confirmation_store = ConfirmationStore()
