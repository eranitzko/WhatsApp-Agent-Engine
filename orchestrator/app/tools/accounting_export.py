"""XLSX ledger export — supports filter_phone and report format config."""

from __future__ import annotations

import io
from datetime import date as date_type
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.db.models import LedgerEntry

_DATE_FORMATS = {
    "DD/MM/YYYY": lambda d: d.strftime("%d/%m/%Y") if d else "",
    "YYYY-MM-DD": lambda d: d.isoformat() if d else "",
    "DD MMM YYYY": lambda d: d.strftime("%d %b %Y") if d else "",
}


def _phone_to_name_from_db(db, group_jid: str) -> dict[str, str]:
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid).all()
    household = {r.phone for r in rows if r.is_household}
    result = {}
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        result[r.phone] = "Parents" if r.phone in household else name
    return result


def _fmt_date(d: date_type | None, date_format: str) -> str:
    formatter = _DATE_FORMATS.get(date_format, _DATE_FORMATS["YYYY-MM-DD"])
    return formatter(d)


def _fmt_currency(amount: float, currency_display: str) -> str:
    if currency_display == "₪":
        return f"₪{amount:.2f}"
    return f"{amount:.2f} ILS"


def generate_ledger_xlsx(
    group_jid: str,
    filter_phone: str | None = None,
    fmt_config: dict | None = None,
) -> bytes:
    cfg = fmt_config or {}
    sections: list[str] = cfg.get("sections") or ["balances", "transactions"]
    date_format: str = cfg.get("date_format", "YYYY-MM-DD")
    currency_display: str = cfg.get("currency_display", "ILS")
    include_settled: bool = cfg.get("include_settled", True)
    sort_by: str = cfg.get("sort_by", "date")
    grouping: str = cfg.get("grouping", "none")

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid)
        if filter_phone:
            q = q.filter(
                or_(LedgerEntry.from_phone == filter_phone, LedgerEntry.to_phone == filter_phone)
            )
        entries = q.order_by(LedgerEntry.transaction_date).all()
        names = _phone_to_name_from_db(db, group_jid)

    if not include_settled:
        entries = [e for e in entries if (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > Decimal("0")]

    if sort_by == "person":
        entries = sorted(entries, key=lambda e: names.get(e.from_phone, e.from_phone))
    elif sort_by == "amount":
        entries = sorted(entries, key=lambda e: e.amount_ils, reverse=True)

    wb = openpyxl.Workbook()
    first_sheet = True

    if "balances" in sections:
        ws = wb.active if first_sheet else wb.create_sheet("Balances")
        ws.title = "Balances"
        first_sheet = False
        _write_balances_sheet(ws, entries, names, date_format, currency_display)

    if "transactions" in sections:
        ws = wb.active if first_sheet else wb.create_sheet("Transactions")
        ws.title = "Transactions"
        first_sheet = False
        _write_transactions_sheet(ws, entries, names, date_format, currency_display, grouping)

    if "settlements" in sections:
        with SessionLocal() as db:
            from app.db.models import LedgerSettlement
            entry_ids = {e.id for e in entries}
            settlements = (
                db.query(LedgerSettlement)
                .filter(
                    or_(
                        LedgerSettlement.payment_leg_id.in_(entry_ids),
                        LedgerSettlement.debt_leg_id.in_(entry_ids),
                    )
                )
                .all()
            )
        ws = wb.active if first_sheet else wb.create_sheet("Settlements")
        ws.title = "Settlements"
        first_sheet = False
        _write_settlements_sheet(ws, settlements, names, date_format, currency_display)

    if first_sheet:
        # No sections matched — write empty sheet to avoid empty workbook error
        wb.active.title = "Empty"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_net_balances(entries: list, names: dict[str, str]) -> dict[tuple[str, str], Decimal]:
    raw: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        if remaining <= Decimal("0"):
            continue
        frm = names.get(e.from_phone, e.from_phone)
        to = names.get(e.to_phone, e.to_phone)
        if frm == to:
            continue
        key = (frm, to)
        raw[key] = raw.get(key, Decimal("0")) + remaining

    netted: dict[tuple[str, str], Decimal] = {}
    seen: set[tuple[str, str]] = set()
    for (a, b), amt in raw.items():
        canonical = (min(a, b), max(a, b))
        if canonical in seen:
            continue
        seen.add(canonical)
        reverse = raw.get((b, a), Decimal("0"))
        net = amt - reverse
        if net > Decimal("0"):
            netted[(a, b)] = net
        elif net < Decimal("0"):
            netted[(b, a)] = -net
    return netted


def _write_balances_sheet(ws, entries, names, date_format, currency_display) -> None:
    headers = ["Owes", "To", "Amount"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for (frm, to), amt in sorted(_compute_net_balances(entries, names).items()):
        ws.append([frm, to, _fmt_currency(float(amt), currency_display)])


def _write_transactions_sheet(ws, entries, names, date_format, currency_display, grouping) -> None:
    headers = ["Date", "From", "To", "Amount", "Settled", "Remaining", "Description", "Transaction ID"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    last_group_key = None
    for e in entries:
        group_key = None
        if grouping == "month" and e.transaction_date:
            group_key = e.transaction_date.strftime("%Y-%m")
        elif grouping == "person":
            group_key = names.get(e.from_phone, e.from_phone)

        if grouping != "none" and group_key != last_group_key:
            ws.append([f"── {group_key} ──"])
            last_group_key = group_key

        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        ws.append([
            _fmt_date(e.transaction_date, date_format),
            names.get(e.from_phone, e.from_phone),
            names.get(e.to_phone, e.to_phone),
            _fmt_currency(float(e.amount_ils), currency_display),
            _fmt_currency(float(e.amount_settled_ils or Decimal("0")), currency_display),
            _fmt_currency(float(remaining), currency_display),
            e.description,
            e.transaction_id,
        ])


def _write_settlements_sheet(ws, settlements, names, date_format, currency_display) -> None:
    headers = ["Payment Leg ID", "Debt Leg ID", "Amount Applied", "Date"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for s in settlements:
        ws.append([
            s.payment_leg_id[:8],
            s.debt_leg_id[:8],
            _fmt_currency(float(s.amount_ils), currency_display),
            _fmt_date(s.created_at.date() if s.created_at else None, date_format),
        ])


def generate_ledger_pdf(group_jid: str, filter_phone: str | None = None) -> bytes:
    """Generate a PDF ledger summary using ReportLab.

    Produces: net balances table + transaction list.
    Returns raw PDF bytes.
    """
    import io as _io
    from datetime import datetime, timezone
    from decimal import Decimal

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    with SessionLocal() as db:
        names = _phone_to_name_from_db(db, group_jid)
        from app.db.models import LedgerEntry as _LedgerEntry
        query = db.query(_LedgerEntry).filter(_LedgerEntry.group_jid == group_jid)
        if filter_phone:
            from sqlalchemy import or_
            query = query.filter(or_(
                _LedgerEntry.from_phone == filter_phone,
                _LedgerEntry.to_phone == filter_phone,
            ))
        entries = query.order_by(_LedgerEntry.transaction_date.desc()).all()

    styles = getSampleStyleSheet()
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph("<b>Family Ledger</b>", styles["Title"]))
    story.append(Paragraph(f"Generated: {generated}", styles["Normal"]))
    story.append(Spacer(1, 0.4*cm))

    # ── Net balances ──────────────────────────────────────────────────────────
    story.append(Paragraph("<b>Net Balances</b>", styles["Heading2"]))
    net: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        unsettled = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        if unsettled > 0:
            key = (e.from_phone, e.to_phone)
            net[key] = net.get(key, Decimal("0")) + unsettled

    if net:
        bal_data = [["From", "To", "Amount (₪)"]]
        for (frm, to), amt in sorted(net.items()):
            bal_data.append([
                names.get(frm, frm), names.get(to, to), f"₪{float(amt):,.2f}"
            ])
        bal_table = Table(bal_data, colWidths=[5*cm, 5*cm, 4*cm])
        bal_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4A90D9")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ]))
        story.append(bal_table)
    else:
        story.append(Paragraph("All debts settled.", styles["Normal"]))

    story.append(Spacer(1, 0.4*cm))

    # ── Transactions ──────────────────────────────────────────────────────────
    story.append(Paragraph("<b>Transactions</b>", styles["Heading2"]))
    if entries:
        tx_data = [["Date", "From", "To", "Amount", "Settled", "Description"]]
        for e in entries:
            tx_data.append([
                e.transaction_date.isoformat() if e.transaction_date else "",
                names.get(e.from_phone, e.from_phone),
                names.get(e.to_phone, e.to_phone),
                f"₪{float(e.amount_ils):,.2f}",
                f"₪{float(e.amount_settled_ils or 0):,.2f}",
                (e.description or "")[:40],
            ])
        tx_table = Table(tx_data, colWidths=[2.2*cm, 2.5*cm, 2.5*cm, 2.2*cm, 2.2*cm, 4.4*cm])
        tx_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4A90D9")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("ALIGN", (3, 0), (4, -1), "RIGHT"),
        ]))
        story.append(tx_table)
    else:
        story.append(Paragraph("No transactions found.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
