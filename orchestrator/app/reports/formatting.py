"""Shared value-formatting helpers for report generation.

Blueprint code calls these to turn raw Decimal/date values into final display
strings *before* building Row/Cell objects — render_pdf never formats a raw
number or date itself. See
docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md.
"""
from __future__ import annotations

from datetime import date as date_type

_DATE_FORMATS = {
    "DD/MM/YYYY": lambda d: d.strftime("%d/%m/%Y") if d else "",
    "YYYY-MM-DD": lambda d: d.isoformat() if d else "",
    "DD MMM YYYY": lambda d: d.strftime("%d %b %Y") if d else "",
}


def format_date(d: date_type | None, date_format: str) -> str:
    formatter = _DATE_FORMATS.get(date_format, _DATE_FORMATS["YYYY-MM-DD"])
    return formatter(d)


def format_currency(amount: float, currency_display: str) -> str:
    """Sign-aware ILS-only formatter: currency_display picks symbol ("₪") vs
    suffix ("ILS") display style for an amount that is always ILS. Used by
    family_accounting, where amounts can be negative (payments).

    Note: uses thousands separators (:,.2f) — the pre-migration _fmt_currency
    in accounting_export.py used plain .2f without them. This is an
    intentional fix (consolidating with format_amount's existing
    thousands-separator behavior), not an accidental behavior change.
    """
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if currency_display == "₪":
        return f"{sign}₪{amount:,.2f}"
    return f"{sign}{amount:,.2f} ILS"


def format_amount(amount: float | None, currency: str | None) -> str:
    """Multi-currency formatter: ILS gets the ₪ symbol, any other currency
    gets its code as a suffix. Used by invoice_curator, where an invoice's
    original amount can be in any currency and is never negative."""
    if amount is None:
        return "—"
    if currency == "ILS":
        return f"₪{amount:,.2f}"
    return f"{amount:,.2f} {currency or ''}"
