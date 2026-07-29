"""Single source of truth for what counts as a valid invoice amount.

Used at every site that accepts or produces an invoice amount: OCR
extraction (extractor.py), manual entry (exec_save_invoice), and correction
(exec_set_invoice_amount, invoice_tools.py's stage_action pre-validation).
Previously each of those four sites re-implemented this check independently
and drifted out of sync — one relaxed to allow negative amounts (refunds)
while another still rejected them, which is exactly the kind of bug this
module exists to prevent from recurring.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class AmountValidation:
    amount: Decimal | None   # parsed value, or None if invalid
    error: str | None        # None if valid, else a user-facing message


def validate_invoice_amount(value: object) -> AmountValidation:
    """Parse and validate a raw invoice amount.

    Zero is rejected: it's meaningless for an invoice (nothing was charged
    or refunded), and it's also the natural sentinel for a failed OCR read —
    treating it as invalid keeps a genuine "couldn't read this" failure from
    silently masquerading as a real value.

    Negative IS valid — a refund/return/credit.
    """
    if value is None:
        return AmountValidation(None, f"Invalid amount {value!r}.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return AmountValidation(None, f"Invalid amount {value!r}.")
    if amount == 0:
        return AmountValidation(None, "Amount cannot be zero.")
    return AmountValidation(amount, None)


def to_float_or_none(x: Decimal | None) -> float | None:
    """Convert a Decimal to float, preserving None as None — using `is not
    None` rather than truthiness, so a legitimate zero value is never
    silently converted to None (indistinguishable from "missing")."""
    return float(x) if x is not None else None
