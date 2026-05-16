"""XLSX ledger export — two sheets: Balances and Transactions."""

from __future__ import annotations

import io
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

from app.db.session import SessionLocal
from app.db.models import LedgerEntry


def generate_ledger_xlsx(group_jid: str) -> bytes:
    """Return XLSX bytes with two sheets: Balances (net per pair) and Transactions (full log)."""
    with SessionLocal() as db:
        entries = (
            db.query(LedgerEntry)
            .filter_by(group_jid=group_jid)
            .order_by(LedgerEntry.transaction_date)
            .all()
        )

    wb = openpyxl.Workbook()

    ws_bal = wb.active
    ws_bal.title = "Balances"
    _write_balances_sheet(ws_bal, entries)

    ws_tx = wb.create_sheet("Transactions")
    _write_transactions_sheet(ws_tx, entries)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_net_balances(entries: list) -> dict[tuple[str, str], Decimal]:
    """Return net remaining amount per ordered pair (from_phone, to_phone)."""
    raw: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        if remaining <= Decimal("0"):
            continue
        key = (e.from_phone, e.to_phone)
        raw[key] = raw.get(key, Decimal("0")) + remaining

    netted: dict[tuple[str, str], Decimal] = {}
    seen: set[tuple[str, str]] = set()
    for (a, b), amt in raw.items():
        if (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        reverse = raw.get((b, a), Decimal("0"))
        net = amt - reverse
        if net > Decimal("0"):
            netted[(a, b)] = net
        elif net < Decimal("0"):
            netted[(b, a)] = -net
    return netted


def _write_balances_sheet(ws, entries: list) -> None:
    headers = ["Owes", "To", "Amount (ILS)"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for (frm, to), amt in sorted(_compute_net_balances(entries).items()):
        ws.append([frm, to, float(amt)])


def _write_transactions_sheet(ws, entries: list) -> None:
    headers = ["Date", "From", "To", "Amount ILS", "Settled ILS", "Remaining ILS", "Description", "Transaction ID"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for e in entries:
        ws.append([
            e.transaction_date.isoformat() if e.transaction_date else "",
            e.from_phone,
            e.to_phone,
            float(e.amount_ils),
            float(e.amount_settled_ils or Decimal("0")),
            float(e.amount_ils - (e.amount_settled_ils or Decimal("0"))),
            e.description,
            e.transaction_id,
        ])
