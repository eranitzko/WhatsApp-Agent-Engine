"""Multi-party confirmation store for ledger transactions.

Flow:
  1. A tool proposes a transaction that requires confirmation from multiple phones.
  2. The store sends an @mention message to the group.
  3. Each awaiting party replies 'yes' or 'no' in the group.
  4. AgentRunner intercepts their reply and calls confirm() or reject().
  5. All confirmed → the stored commit action is executed.
  6. Any rejection or timeout → whole transaction cancelled, group notified.

TTL: 5 minutes. Scheduler checks for expired pending every 60s.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TTL_SECONDS = 300  # 5 minutes

CONFIRM_WORDS = {"yes", "כן", "confirm", "אישור", "ok", "approve", "יאללה"}
CANCEL_WORDS  = {"no", "לא", "cancel", "ביטול", "abort", "reject"}


@dataclass
class PendingMultiConfirmation:
    id: str
    group_jid: str
    action: str          # "commit_transaction" | "commit_payment"
    commit_params: dict[str, Any]
    description: str     # human-readable summary for cancellation messages
    awaiting: dict[str, bool]  # phone → True (confirmed) / False (pending)
    expires: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    )

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires

    def all_confirmed(self) -> bool:
        return all(self.awaiting.values())

    def pending_phones(self) -> list[str]:
        return [p for p, done in self.awaiting.items() if not done]


class MultiConfirmationStore:
    """Per-group list of pending multi-party confirmations."""

    def __init__(self) -> None:
        self._pending: dict[str, list[PendingMultiConfirmation]] = {}
        # Async callable: (group_jid, text, mentions) → None
        self._sender: Callable[[str, str, list[str]], Awaitable[None]] | None = None

    def set_sender(self, fn: Callable[[str, str, list[str]], Awaitable[None]]) -> None:
        self._sender = fn

    def _list(self, group_jid: str) -> list[PendingMultiConfirmation]:
        if group_jid not in self._pending:
            self._pending[group_jid] = []
        return self._pending[group_jid]

    async def propose(
        self,
        group_jid: str,
        awaiting_phones: list[str],
        action: str,
        commit_params: dict[str, Any],
        description: str,
    ) -> PendingMultiConfirmation:
        mc = PendingMultiConfirmation(
            id=str(uuid.uuid4())[:8],
            group_jid=group_jid,
            action=action,
            commit_params=commit_params,
            description=description,
            awaiting={p: False for p in awaiting_phones},
        )
        self._list(group_jid).append(mc)

        # Build @mention message and send directly to group
        mention_text = (
            "\n".join(f"@{p}" for p in awaiting_phones)
            + f"\n{description}\n"
            + "Reply 'yes' to confirm or 'no' to reject (expires in 5 minutes)."
        )
        mentions = [f"{p}@s.whatsapp.net" for p in awaiting_phones]
        if self._sender:
            try:
                await self._sender(group_jid, mention_text, mentions)
            except Exception:
                logger.exception("multi_confirmation: failed to send mention for %s", mc.id)

        return mc

    def find_for_phone(self, group_jid: str, phone: str) -> PendingMultiConfirmation | None:
        for mc in self._list(group_jid):
            if not mc.is_expired() and phone in mc.awaiting and not mc.awaiting[phone]:
                return mc
        return None

    def confirm(self, group_jid: str, phone: str) -> tuple[str, PendingMultiConfirmation | None]:
        """Mark phone as confirmed. Returns ('all_confirmed'|'waiting'|'not_found', mc)."""
        mc = self.find_for_phone(group_jid, phone)
        if mc is None:
            return "not_found", None
        mc.awaiting[phone] = True
        if mc.all_confirmed():
            self._list(group_jid).remove(mc)
            return "all_confirmed", mc
        return "waiting", mc

    def reject(self, group_jid: str, phone: str) -> PendingMultiConfirmation | None:
        """Cancel the pending transaction this phone is part of. Returns the cancelled mc."""
        mc = self.find_for_phone(group_jid, phone)
        if mc is None:
            return None
        try:
            self._list(group_jid).remove(mc)
        except ValueError:
            pass
        return mc

    def drain_expired(self) -> list[PendingMultiConfirmation]:
        """Remove and return all expired pending confirmations (for scheduler cleanup)."""
        expired = []
        for group_jid, lst in self._pending.items():
            still_valid = []
            for mc in lst:
                if mc.is_expired():
                    expired.append(mc)
                else:
                    still_valid.append(mc)
            self._pending[group_jid] = still_valid
        return expired

    @staticmethod
    def is_confirm(text: str) -> bool:
        return text.strip().lower() in CONFIRM_WORDS

    @staticmethod
    def is_cancel(text: str) -> bool:
        return text.strip().lower() in CANCEL_WORDS


# Singleton
multi_confirmation_store = MultiConfirmationStore()
