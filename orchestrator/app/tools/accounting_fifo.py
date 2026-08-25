"""FIFO settlement logic, fully testable in isolation — no DB access in the
core logic (DebtLeg, apply_payment). This module has also become the shared
home for other pure, DB-independent accounting utilities that don't belong
to any one blueprint's tool file (split_evenly, net_pair) — consolidated
here rather than each getting reinvented per call site. The one exception
that DOES query the DB is fetch_open_debt_legs below; it lives here anyway
since it's the natural counterpart to apply_payment (see its own docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def split_evenly(total: Decimal, n: int, rounding: str = ROUND_HALF_UP) -> list[Decimal]:
    """Split total into n equal shares, each rounded to 2 decimal places.

    Does NOT redistribute leftover cents from rounding — each share is
    computed independently as (total / n).quantize(...). This matches the
    pre-existing behavior of every caller being consolidated here; whether
    remainder cents should instead be distributed to make shares sum
    exactly to total is a separate product decision, out of scope for this
    consolidation.
    """
    per_person = (total / Decimal(n)).quantize(Decimal("0.01"), rounding=rounding)
    return [per_person] * n


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


def net_pair(a: str, b: str, net: Decimal) -> tuple[str, str, Decimal] | None:
    """Resolve a signed net amount between two parties into one directed
    (debtor, creditor, amount) line, or None if they're exactly settled.

    `net` is the amount a owes b minus the amount b owes a: positive means
    a is the net debtor, negative means b is, zero means fully offset (the
    caller should treat this as "nothing to show", not a $0 line).

    This is the "net two directed amounts between A and B into one signed
    line" shape that was independently duplicated (with an
    `if net > 0: ... elif net < 0: ... else: ...` idiom) across
    get_balance's two-phone/household/per-partner cases, get_debt_summary,
    account_service.balance_update_message, and accounting_export's
    _compute_net_balances. Callers that start from two raw directed amounts
    (e.g. a_owes_b, b_owes_a) should subtract them first:
    net_pair(a, b, a_owes_b - b_owes_a).
    """
    if net > Decimal("0"):
        return (a, b, net)
    elif net < Decimal("0"):
        return (b, a, -net)
    return None


def fetch_open_debt_legs(
    db, group_jid: str, from_phone: str | set[str], to_phone: str | set[str],
    household_id: str | None = None,
) -> list[DebtLeg]:
    """Query open (partially/fully unsettled) LedgerEntry rows for a directed
    (from_phone, to_phone) pair, ordered oldest-first, as DebtLeg objects
    ready for apply_payment(). Requires DB access, unlike the rest of this
    module — kept here anyway since it's the natural counterpart to
    apply_payment, and this exact query+construction pattern was previously
    duplicated between account_service.py and agent_runner.py.

    from_phone/to_phone each accept either a single phone or a set of phones
    (e.g. AccountService.get_joint_pool's result) — passing a pool matches a
    debt owed by/to ANY member of it, so a payment named to one joint-account
    member can settle a debt actually owed to another member of the same
    pool. A single string behaves exactly as before (matches only itself)."""
    from app.db.models import LedgerEntry
    from_phones = {from_phone} if isinstance(from_phone, str) else set(from_phone)
    to_phones = {to_phone} if isinstance(to_phone, str) else set(to_phone)
    q = db.query(LedgerEntry).filter(
        LedgerEntry.from_phone.in_(from_phones),
        LedgerEntry.to_phone.in_(to_phones),
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
