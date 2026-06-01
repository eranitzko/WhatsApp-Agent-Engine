"""FIFO correction queue for ledger transaction corrections.

Design:
- One queue per group_jid.
- Max capacity: num_admins + 1 slots (queried at enqueue time).
- Each admin can have at most one pending correction at a time.
- Corrections expire after TTL_MINUTES if not confirmed.
- Corrections are applied in FIFO order when confirmed.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

TTL_MINUTES = 10


@dataclass
class PendingCorrection:
    token: str
    admin_phone: str
    transaction_id: str
    changes: dict[str, Any]
    description: str
    expires: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=TTL_MINUTES))

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires


class CorrectionQueue:
    """Per-group FIFO queue of pending ledger corrections."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[PendingCorrection]] = {}

    def _queue(self, group_jid: str) -> deque[PendingCorrection]:
        if group_jid not in self._queues:
            self._queues[group_jid] = deque()
        return self._queues[group_jid]

    def _purge_expired(self, group_jid: str) -> None:
        q = self._queue(group_jid)
        while q and q[0].is_expired():
            q.popleft()

    def enqueue(
        self,
        group_jid: str,
        admin_phone: str,
        transaction_id: str,
        changes: dict[str, Any],
        description: str,
        max_slots: int,
    ) -> PendingCorrection | str:
        """Add a correction to the queue. Returns the PendingCorrection or an error string."""
        self._purge_expired(group_jid)
        q = self._queue(group_jid)

        if any(c.admin_phone == admin_phone for c in q):
            return "You already have a pending correction. Confirm or cancel it first."

        if len(q) >= max_slots:
            return f"Correction queue is full ({max_slots} slots). Wait for pending corrections to complete."

        correction = PendingCorrection(
            token=str(uuid.uuid4())[:8],
            admin_phone=admin_phone,
            transaction_id=transaction_id,
            changes=changes,
            description=description,
        )
        q.append(correction)
        return correction

    def peek_first(self, group_jid: str) -> PendingCorrection | None:
        """Return the first (oldest) pending correction without removing it."""
        self._purge_expired(group_jid)
        q = self._queue(group_jid)
        return q[0] if q else None

    def get_by_token(self, group_jid: str, token: str) -> PendingCorrection | None:
        self._purge_expired(group_jid)
        for c in self._queue(group_jid):
            if c.token == token:
                return c
        return None

    def remove(self, group_jid: str, token: str) -> bool:
        """Remove a correction by token. Returns True if found and removed."""
        q = self._queue(group_jid)
        for i, c in enumerate(q):
            if c.token == token:
                del q[i]
                return True
        return False

    def list_pending(self, group_jid: str) -> list[PendingCorrection]:
        self._purge_expired(group_jid)
        return list(self._queue(group_jid))


# Singleton
correction_queue = CorrectionQueue()
