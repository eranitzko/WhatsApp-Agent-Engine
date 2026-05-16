"""XLSX ledger export — two sheets: Balances and Transactions."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

from app.config import settings
from app.db.session import SessionLocal
from app.db.models import LedgerEntry


def _phone_to_name() -> dict[str, str]:
    """Return phone → display name. Household members all map to 'Parents'."""
    try:
        members = json.loads(settings.family_members_json) if settings.family_members_json else {}
    except Exception:
        members = {}
    household: set[str] = set()
    if settings.family_household_members and members:
        hnames = [n.strip() for n in settings.family_household_members.split(",") if n.strip()]
        household = {members[n] for n in hnames if n in members}
    return {phone: ("Parents" if phone in household else name) for name, phone in members.items()}


def generate_ledger_xlsx(group_jid: str) -> bytes:
    """Return XLSX bytes with two sheets: Balances (net per pair) and Transactions (full log)."""
    with SessionLocal() as db:
        entries = (
            db.query(LedgerEntry)
            .filter_by(group_jid=group_jid)
            .order_by(LedgerEntry.transaction_date)
            .all()
        )

    names = _phone_to_name()
    wb = openpyxl.Workbook()

    ws_bal = wb.active
    ws_bal.title = "Balances"
    _write_balances_sheet(ws_bal, entries, names)

    ws_tx = wb.create_sheet("Transactions")
    _write_transactions_sheet(ws_tx, entries, names)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_net_balances(entries: list, names: dict[str, str]) -> dict[tuple[str, str], Decimal]:
    """Return net remaining amount per display-name pair (owes, owed_by)."""
    raw: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))
        if remaining <= Decimal("0"):
            continue
        frm = names.get(e.from_phone, e.from_phone)
        to = names.get(e.to_phone, e.to_phone)
        if frm == to:
            continue  # intra-household entry — skip
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


def _write_balances_sheet(ws, entries: list, names: dict[str, str]) -> None:
    headers = ["Owes", "To", "Amount (ILS)"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for (frm, to), amt in sorted(_compute_net_balances(entries, names).items()):
        ws.append([frm, to, float(amt)])


def _write_transactions_sheet(ws, entries: list, names: dict[str, str]) -> None:
    headers = ["Date", "From", "To", "Amount ILS", "Settled ILS", "Remaining ILS", "Description", "Transaction ID"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for e in entries:
        ws.append([
            e.transaction_date.isoformat() if e.transaction_date else "",
            names.get(e.from_phone, e.from_phone),
            names.get(e.to_phone, e.to_phone),
            float(e.amount_ils),
            float(e.amount_settled_ils or Decimal("0")),
            float(e.amount_ils - (e.amount_settled_ils or Decimal("0"))),
            e.description,
            e.transaction_id,
        ])
