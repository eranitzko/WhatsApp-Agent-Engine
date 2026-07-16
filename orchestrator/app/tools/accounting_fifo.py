"""FIFO settlement logic, fully testable in isolation — no DB access in the
core logic (DebtLeg, apply_payment). The one deliberate exception is
fetch_open_debt_legs below, which does query the DB; it lives here anyway
since it's the natural counterpart to apply_payment (see its own docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class DebtLeg:
    id: str
    amount_ils: Decimal
    amount_settled_ils: Decimal
    transaction_date: date

    @property
    def remaining_ils(self) -> Decimal:
        # Matches LedgerEntry.remaining_ils's null-guard (app/db/models.py) —
        # this dataclass's core logic is deliberately DB-independent (see
        # module docstring), so it can't import that property directly, but
        # the two must stay behaviorally identical or FIFO settlement can
        # raise where every other consumer of the same value silently
        # defaults to zero.
        return self.amount_ils - (self.amount_settled_ils or Decimal("0"))


@dataclass
class SettlementResult:
    settlements: list[tuple[str, Decimal]]   # (debt_leg_id, amount_applied)
    updated_legs: list[tuple[str, Decimal]]  # (debt_leg_id, new amount_settled_ils)
    leftover: Decimal                        # unspent remainder after all debts covered


def apply_payment(payment_amount: Decimal, open_debts: list[DebtLeg]) -> SettlementResult:
    """Apply payment_amount to open_debts FIFO (oldest transaction_date first).

    Args:
        payment_amount: Total ILS amount to distribute.
        open_debts: Debt legs ordered by transaction_date ASC (caller's responsibility).

    Returns:
        SettlementResult describing which legs were settled and by how much.
    """
    remaining = payment_amount
    settlements: list[tuple[str, Decimal]] = []
    updated_legs: list[tuple[str, Decimal]] = []

    for debt in open_debts:
        if remaining <= Decimal("0"):
            break
        if debt.remaining_ils <= Decimal("0"):
            continue
        apply = min(debt.remaining_ils, remaining)
        settlements.append((debt.id, apply))
        updated_legs.append((debt.id, debt.amount_settled_ils + apply))
        remaining -= apply

    return SettlementResult(
        settlements=settlements,
        updated_legs=updated_legs,
        leftover=remaining,
    )


def fetch_open_debt_legs(db, group_jid: str, from_phone: str, to_phone: str, household_id: str | None = None) -> list[DebtLeg]:
    """Query open (partially/fully unsettled) LedgerEntry rows for a directed
    (from_phone, to_phone) pair, ordered oldest-first, as DebtLeg objects
    ready for apply_payment(). Requires DB access, unlike the rest of this
    module — kept here anyway since it's the natural counterpart to
    apply_payment, and this exact query+construction pattern was previously
    duplicated between account_service.py and agent_runner.py."""
    from app.db.models import LedgerEntry
    q = db.query(LedgerEntry).filter(
        LedgerEntry.from_phone == from_phone,
        LedgerEntry.to_phone == to_phone,
        LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
    )
    if household_id:
        q = q.filter(LedgerEntry.household_id == household_id)
    else:
        q = q.filter(LedgerEntry.group_jid == group_jid)
    rows = q.order_by(LedgerEntry.transaction_date).all()
    return [
        DebtLeg(
            id=r.id,
            amount_ils=r.amount_ils,
            amount_settled_ils=r.amount_settled_ils or Decimal("0"),
            transaction_date=r.transaction_date,
        )
        for r in rows
    ]
