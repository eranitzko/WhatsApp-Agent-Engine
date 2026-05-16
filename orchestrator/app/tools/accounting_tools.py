"""Family Accounting tools in ToolRegistry format."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_

from app.db.models import LedgerEntry, LedgerSettlement, ScheduledMessage
from app.db.session import SessionLocal
from app.tools.accounting_export import generate_ledger_xlsx
from app.tools.accounting_fifo import DebtLeg, apply_payment
from app.tools.accounting_fx import to_ils

logger = logging.getLogger(__name__)

# ── Schemas ───────────────────────────────────────────────────────────────────

_SCHEMAS: dict[str, dict] = {
    "record_transaction": {
        "name": "record_transaction",
        "description": (
            "Record that someone paid for others. Claude extracts payer, participants, "
            "amount, currency, description, and date from natural language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_phone": {"type": "string", "description": "Phone of the person who paid"},
                "participant_phones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Phones of people who owe the payer (excluding the payer)",
                },
                "amount": {"type": "number", "description": "Total amount paid"},
                "currency": {"type": "string", "description": "ISO 4217 code, e.g. ILS, USD, EUR"},
                "description": {"type": "string", "description": "What the payment was for"},
                "transaction_date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD; defaults to today if omitted",
                },
            },
            "required": ["payer_phone", "participant_phones", "amount", "currency", "description"],
        },
    },
    "record_payment": {
        "name": "record_payment",
        "description": "Record a debt repayment. Applies FIFO settlement to open debt legs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_phone": {"type": "string", "description": "Phone making the payment"},
                "payee_phone": {"type": "string", "description": "Phone receiving payment"},
                "amount_ils": {"type": "number", "description": "Payment amount in ILS"},
                "payment_date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD; defaults to today if omitted",
                },
            },
            "required": ["payer_phone", "payee_phone", "amount_ils"],
        },
    },
    "get_balance": {
        "name": "get_balance",
        "description": (
            "Get net balance. With phone_a only: all open balances for that person. "
            "With phone_a and phone_b: net balance between them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_a": {"type": "string", "description": "First person's phone"},
                "phone_b": {"type": "string", "description": "Second person's phone (optional)"},
            },
            "required": ["phone_a"],
        },
    },
    "get_history": {
        "name": "get_history",
        "description": "Get itemized transaction history, optionally filtered by person and/or date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Filter to transactions involving this phone (optional)"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD (optional)"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)"},
            },
            "required": [],
        },
    },
    "export_ledger": {
        "name": "export_ledger",
        "description": "Generate an XLSX with full balances and transaction history and email it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to send the export to"},
            },
            "required": ["email"],
        },
    },
    "set_reminder": {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder WhatsApp message for the sender at a future time. "
            "Only the sender can set their own reminders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The reminder text"},
                "send_at": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-06-01T09:00:00"},
            },
            "required": ["message", "send_at"],
        },
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _net_owed(db, group_jid: str, from_phone: str, to_phone: str) -> Decimal:
    """Total remaining amount from_phone owes to_phone in this group."""
    rows = (
        db.query(LedgerEntry)
        .filter(
            LedgerEntry.group_jid == group_jid,
            LedgerEntry.from_phone == from_phone,
            LedgerEntry.to_phone == to_phone,
        )
        .all()
    )
    return sum((r.amount_ils - (r.amount_settled_ils or Decimal("0")) for r in rows), Decimal("0"))


# ── Executors ─────────────────────────────────────────────────────────────────

async def _exec_record_transaction(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    payer = params["payer_phone"]
    participants = params["participant_phones"]
    amount = Decimal(str(params["amount"]))
    currency = params.get("currency", "ILS")
    description = params.get("description", "")
    tx_date_str = params.get("transaction_date") or date.today().isoformat()
    tx_date = date.fromisoformat(tx_date_str)

    try:
        amount_ils = await to_ils(amount, currency, tx_date)
    except RuntimeError as exc:
        return str(exc)

    if not participants:
        return "Error: participant_phones must not be empty."

    per_person = (amount_ils / Decimal(len(participants))).quantize(Decimal("0.01"))
    desc_with_fx = (
        f"{description} (original: {amount} {currency.upper()})"
        if currency.upper() != "ILS"
        else description
    )
    transaction_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        for phone in participants:
            db.add(LedgerEntry(
                transaction_id=transaction_id,
                group_jid=group_jid,
                from_phone=phone,
                to_phone=payer,
                amount_ils=per_person,
                amount_settled_ils=Decimal("0"),
                description=desc_with_fx,
                transaction_date=tx_date,
                created_at=now,
            ))
        db.commit()

    split_info = (
        f"split equally {per_person:.2f} ILS each among {len(participants)} people"
        if len(participants) > 1
        else f"{amount_ils:.2f} ILS"
    )
    return f"Recorded: {payer} paid for {', '.join(participants)} — {split_info}. (tx: {transaction_id[:8]})"


async def _exec_record_payment(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    payer = params["payer_phone"]
    payee = params["payee_phone"]
    amount_ils = Decimal(str(params["amount_ils"]))
    pay_date_str = params.get("payment_date") or date.today().isoformat()
    pay_date = date.fromisoformat(pay_date_str)
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        open_rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.from_phone == payer,
                LedgerEntry.to_phone == payee,
                LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
            )
            .order_by(LedgerEntry.transaction_date)
            .all()
        )

        debt_legs = [
            DebtLeg(
                id=r.id,
                amount_ils=r.amount_ils,
                amount_settled_ils=r.amount_settled_ils or Decimal("0"),
                transaction_date=r.transaction_date,
            )
            for r in open_rows
        ]

        result = apply_payment(amount_ils, debt_legs)

        for leg_id, new_settled in result.updated_legs:
            row = db.get(LedgerEntry, leg_id)
            row.amount_settled_ils = new_settled

        payment_leg = LedgerEntry(
            transaction_id=str(uuid.uuid4()),
            group_jid=group_jid,
            from_phone=payee,
            to_phone=payer,
            amount_ils=amount_ils,
            amount_settled_ils=amount_ils,
            description=f"Payment on {pay_date.isoformat()}",
            transaction_date=pay_date,
            created_at=now,
        )
        db.add(payment_leg)
        db.flush()

        for debt_leg_id, applied_amount in result.settlements:
            db.add(LedgerSettlement(
                payment_leg_id=payment_leg.id,
                debt_leg_id=debt_leg_id,
                amount_ils=applied_amount,
                created_at=now,
            ))
        db.commit()

    parts = [f"{amt:.2f} ILS off debt {did[:8]}" for did, amt in result.settlements]
    summary = "; ".join(parts) if parts else "no open debts found to settle"
    leftover = f" (overpaid by {result.leftover:.2f} ILS)" if result.leftover > 0 else ""
    return f"Payment of {amount_ils:.2f} ILS recorded. {summary}.{leftover}"


async def _exec_get_balance(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    phone_a = params["phone_a"]
    phone_b = params.get("phone_b")

    with SessionLocal() as db:
        if phone_b:
            a_owes_b = _net_owed(db, group_jid, phone_a, phone_b)
            b_owes_a = _net_owed(db, group_jid, phone_b, phone_a)
            net = a_owes_b - b_owes_a
            if net > Decimal("0"):
                return f"{phone_a} owes {phone_b}: {net:.2f} ILS"
            elif net < Decimal("0"):
                return f"{phone_b} owes {phone_a}: {(-net):.2f} ILS"
            return f"{phone_a} and {phone_b} are settled up."

        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                or_(LedgerEntry.from_phone == phone_a, LedgerEntry.to_phone == phone_a),
            )
            .all()
        )
        partners = {
            r.from_phone if r.to_phone == phone_a else r.to_phone
            for r in rows
        }

        lines = []
        for partner in sorted(partners):
            a_owes = _net_owed(db, group_jid, phone_a, partner)
            p_owes = _net_owed(db, group_jid, partner, phone_a)
            net = a_owes - p_owes
            if net > Decimal("0"):
                lines.append(f"{phone_a} owes {partner}: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"{partner} owes {phone_a}: {(-net):.2f} ILS")

        return "\n".join(lines) if lines else f"No open balances for {phone_a}."


async def _exec_get_history(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    phone = params.get("phone")
    from_date = params.get("from_date")
    to_date = params.get("to_date")

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid)
        if phone:
            q = q.filter(or_(LedgerEntry.from_phone == phone, LedgerEntry.to_phone == phone))
        if from_date:
            q = q.filter(LedgerEntry.transaction_date >= date.fromisoformat(from_date))
        if to_date:
            q = q.filter(LedgerEntry.transaction_date <= date.fromisoformat(to_date))
        rows = q.order_by(LedgerEntry.transaction_date).all()

    if not rows:
        return "No transactions found."

    lines = []
    for r in rows:
        remaining = r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        status = "settled" if remaining <= Decimal("0") else f"{remaining:.2f} ILS remaining"
        lines.append(
            f"{r.transaction_date} | {r.from_phone} → {r.to_phone} | "
            f"{r.amount_ils:.2f} ILS | {status} | {r.description}"
        )
    return "\n".join(lines)


async def _exec_export_ledger(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    email = params["email"]

    try:
        xlsx_bytes = generate_ledger_xlsx(group_jid)
    except Exception as exc:
        logger.exception("export_ledger: XLSX generation failed")
        return f"Failed to generate report: {exc}"

    try:
        from app.mailer.gmail import send_report_email
        send_report_email(
            to=email,
            subject="Family Ledger Export",
            body="Your family ledger export is attached.",
            attachments=[("ledger.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes)],
        )
    except Exception as exc:
        logger.exception("export_ledger: email failed")
        return f"Report generated but failed to send email: {exc}"

    return f"Ledger exported and sent to {email}."


async def _exec_set_reminder(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    sender = ctx.get("sender", "")
    to_phone = sender.split("@")[0].split(":")[0]
    message = params["message"]
    send_at_str = params["send_at"]

    try:
        send_at = datetime.fromisoformat(send_at_str)
        if send_at.tzinfo is None:
            send_at = send_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return f"Invalid datetime: '{send_at_str}'. Use ISO 8601, e.g. 2026-06-01T09:00:00."

    now = datetime.now(timezone.utc)
    if send_at <= now:
        return "send_at must be in the future."

    with SessionLocal() as db:
        db.add(ScheduledMessage(
            group_jid=group_jid,
            to_phone=to_phone,
            message=message,
            send_at=send_at,
            sent=False,
            created_at=now,
        ))
        db.commit()

    return f"Reminder set for {send_at.isoformat()}: \"{message}\""


# ── Public factory ─────────────────────────────────────────────────────────────

def get_accounting_tools() -> dict[str, dict]:
    """Return all 6 accounting tools in ToolRegistry format."""
    return {
        name: {"schema": _SCHEMAS[name], "executor": executor}
        for name, executor in [
            ("record_transaction", _exec_record_transaction),
            ("record_payment",     _exec_record_payment),
            ("get_balance",        _exec_get_balance),
            ("get_history",        _exec_get_history),
            ("export_ledger",      _exec_export_ledger),
            ("set_reminder",       _exec_set_reminder),
        ]
    }
