"""Pending confirmation state for destructive or external actions.

Flow:
  1. Agent calls stage_action(action, params, description).
  2. System stores a PendingAction for the group with a 5-min TTL.
  3. Next admin message:
       - "yes" / "כן" / "confirm" / "אישור" → execute the stored action.
       - anything else (including a new image) → cancel and process normally.
  4. On cancel or expiry, a cancellation note is returned.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agent.reply_words import is_affirmative, is_negative

TTL_MINUTES = 5


@dataclass
class PendingAction:
    action: str                     # e.g. "remove_invoice", "send_email"
    params: dict[str, Any]          # arguments needed to execute
    description: str                # human-readable summary shown to admin
    staged_by: str = ""             # phone of the user who requested this action; "" = any member can confirm
    expires: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=TTL_MINUTES))

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires


class ConfirmationStore:
    """One pending action per group at a time.

    NOTE: State is in-memory only. A server restart between stage_action
    and the user's "yes" will silently lose the pending action.
    This is intentional for the short 5-min TTL — the window is too narrow to
    justify DB persistence. Cross-group accounting confirmations use the
    CrossGroupConfirmation table (persistent) instead.
    """

    def __init__(self) -> None:
        self._store: dict[str, PendingAction] = {}

    def set(self, group_id: str, action: str, params: dict, description: str, staged_by: str = "") -> bool:
        """Stage a new pending action. Returns False without overwriting if one already exists.

        Callers should surface a 'confirm or cancel existing action first' message when
        False is returned.
        """
        existing = self.get(group_id)
        if existing is not None:
            return False
        self._store[group_id] = PendingAction(
            action=action, params=params, description=description, staged_by=staged_by,
        )
        return True

    def get(self, group_id: str) -> PendingAction | None:
        pending = self._store.get(group_id)
        if pending and pending.is_expired():
            del self._store[group_id]
            return None
        return pending

    def clear(self, group_id: str) -> None:
        self._store.pop(group_id, None)

    def is_confirm(self, text: str) -> bool:
        return is_affirmative(text)

    def is_cancel(self, text: str) -> bool:
        return is_negative(text)


# Singleton
confirmation_store = ConfirmationStore()
