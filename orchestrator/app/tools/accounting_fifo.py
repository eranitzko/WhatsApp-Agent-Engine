"""Pure FIFO settlement logic — no DB access, fully testable in isolation."""

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
        return self.amount_ils - self.amount_settled_ils


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
