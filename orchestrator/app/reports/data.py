"""Query and structure invoice data for report generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_

from app.db.models import Invoice
from app.db.session import SessionLocal

_TZ_IL = ZoneInfo("Asia/Jerusalem")


@dataclass
class ReportRow:
    id: str
    invoice_date: date | None
    invoice_number: str | None
    vendor: str | None
    description: str | None
    amount_original: Decimal | None
    currency_original: str | None
    amount_ils: Decimal | None
    flagged: bool
    flag_reason: str | None
    r2_key: str | None


@dataclass
class ReportData:
    group_id: str
    month: int
    year: int
    rows: list[ReportRow]
    # Dual-currency: True if any row has a non-ILS currency OR forced by config
    show_dual_currency: bool
    total_ils: Decimal
    currencies: list[str]  # sorted unique list of original currencies
    period_label: str = ""  # human-readable period (overrides month/year default when set)


def fetch_report_data(
    group_id: str,
    month: int = None,
    year: int = None,
    force_dual_currency: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ReportData:
    """Query DB for invoices in the given period and build a ReportData.

    Pass month+year for a calendar month, or start_date+end_date for a custom range.
    """
    if start_date and end_date:
        # Custom date range — end_date is inclusive, so add 1 day for the exclusive upper bound
        from datetime import timedelta
        month_start  = start_date
        month_end    = end_date + timedelta(days=1)
        # Represent with the start month/year for backward compat
        month = start_date.month
        year  = start_date.year
        period_label = f"{start_date.strftime('%d/%m/%Y')} – {end_date.strftime('%d/%m/%Y')}"
    else:
        # Calendar month — use Israel timezone so month boundaries match local calendar
        now = datetime.now(_TZ_IL)
        month = month or now.month
        year  = year  or now.year
        month_start = date(year, month, 1)
        month_end   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        period_label = ""

    # Build tz-aware datetimes for the received_at fallback filter so the comparison
    # is consistent with the timezone-aware received_at column (UTC).
    received_start = datetime(month_start.year, month_start.month, month_start.day, tzinfo=_TZ_IL)
    received_end   = datetime(month_end.year,   month_end.month,   month_end.day,   tzinfo=_TZ_IL)

    with SessionLocal() as db:
        invoices = (
            db.query(Invoice)
            .filter(
                Invoice.group_id == group_id,
                or_(
                    # Invoice has a date and it falls in the period
                    and_(Invoice.invoice_date >= month_start, Invoice.invoice_date < month_end),
                    # Invoice has no date — use received_at as fallback (tz-aware comparison)
                    and_(Invoice.invoice_date == None, Invoice.received_at >= received_start, Invoice.received_at < received_end),  # noqa: E711
                ),
            )
            .order_by(Invoice.invoice_date, Invoice.created_at)
            .all()
        )

        rows = [
            ReportRow(
                id=inv.id,
                invoice_date=inv.invoice_date,
                invoice_number=inv.invoice_number,
                vendor=inv.vendor,
                description=inv.description,
                amount_original=Decimal(str(inv.amount_original)) if inv.amount_original else None,
                currency_original=inv.currency_original,
                amount_ils=Decimal(str(inv.amount_ils)) if inv.amount_ils else None,
                flagged=inv.flagged,
                flag_reason=inv.flag_reason,
                r2_key=inv.r2_key,
            )
            for inv in invoices
        ]

    currencies = sorted({r.currency_original for r in rows if r.currency_original})
    has_foreign = any(c != "ILS" for c in currencies)
    show_dual = force_dual_currency or has_foreign

    total_ils = sum(r.amount_ils for r in rows if r.amount_ils) or Decimal("0")

    return ReportData(
        group_id=group_id,
        month=month,
        year=year,
        rows=rows,
        show_dual_currency=show_dual,
        total_ils=total_ils,
        currencies=currencies,
        period_label=period_label,
    )
