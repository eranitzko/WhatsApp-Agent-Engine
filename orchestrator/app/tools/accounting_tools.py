"""Family Accounting tools in ToolRegistry format."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_

from app.db.models import (
    LedgerEntry, LedgerSettlement, ScheduledMessage, UserProfile, ReportFormat,
)
from app.db.session import SessionLocal
from app.tools.accounting_fifo import DebtLeg, apply_payment
from app.tools.accounting_fx import to_ils
from app.agent.correction_queue import correction_queue

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.accounting.account_service import AccountService

_account_service: "AccountService | None" = None


def set_account_service(service: "AccountService | None") -> None:
    global _account_service
    _account_service = service


# ── DB helpers ────────────────────────────────────────────────────────────────

def _household_phones_from_db(db, group_jid: str) -> set[str]:
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid, is_household=True).all()
    return {r.phone for r in rows}


def _phone_to_name_from_db(db, group_jid: str) -> dict[str, str]:
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid).all()
    household = {r.phone for r in rows if r.is_household}
    result = {}
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        result[r.phone] = "Parents" if r.phone in household else name
    return result


def _count_admins(db) -> int:
    from app.db.models import AdminNumbers
    return db.query(AdminNumbers).count()


def _sender_phone(ctx: dict) -> str:
    sender = ctx.get("sender", "")
    return sender.split("@")[0].split(":")[0]


def _net_owed(db, group_jid: str, from_phone: str, to_phone: str) -> Decimal:
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


# ── Schemas ───────────────────────────────────────────────────────────────────

_SCHEMAS: dict[str, dict] = {
    "record_expense": {
        "name": "record_expense",
        "category": "accounting",
        "access": "user",
        "description": (
            "Use when one person paid for others, or a user acknowledges a debt. "
            "Examples: 'Eran paid for me ₪150', 'I owe Tal ₪200', 'I paid for Eden'. "
            "Handles routing automatically: 1st-party (self-reported debt) is recorded immediately; "
            "2nd-party (claimed credit at another's expense) sends a confirmation request first. "
            "Returns: 'Recorded.' or 'Confirmation request sent to [Name].'"
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
        "category": "accounting",
        "access": "user",
        "description": (
            "Use when a user reports repaying a debt (e.g. 'I paid Tal back ₪200', 'Eden sent me money'). "
            "Applies FIFO settlement to open debt legs between the two parties. "
            "Returns: payment recorded with amount and which debt legs were settled."
        ),
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
        "category": "accounting",
        "access": "user",
        "description": (
            "Use when a user asks what they owe, what they are owed, or the balance between two people. "
            "Non-admins automatically see only their own balances. "
            "Returns: net balance per counterparty, or the net amount between two specific people."
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
    "get_debt_summary": {
        "name": "get_debt_summary",
        "category": "accounting",
        "access": "user",
        "description": (
            "Returns a human-readable list of who owes what to whom. "
            "Non-admins see only debts involving themselves. "
            "Admins see the full group debt table. "
            "Returns: each open debt with debtor phone, creditor phone, net ILS owed, and oldest open date."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "get_history": {
        "name": "get_history",
        "category": "accounting",
        "access": "user",
        "description": (
            "Use when a user wants an itemized list of transactions — past expenses, payments, dates. "
            "Non-admins see only their own transactions. Optionally filtered by person or date range. "
            "Returns: dated list of transactions with amounts, parties, and remaining balance per entry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Filter to transactions involving this phone (optional; ignored for non-admins)"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD (optional)"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)"},
            },
            "required": [],
        },
    },
    "get_transaction": {
        "name": "get_transaction",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Returns full detail for a single transaction: all participants, amounts, "
            "date, description, and settlement status. Admin only. "
            "Use the transaction_id prefix (at least 8 characters) shown in get_history. "
            "Use this before correct_transaction to confirm you have the right record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {
                    "type": "string",
                    "description": "Transaction ID prefix (at least 8 characters) from get_history.",
                },
            },
            "required": ["transaction_id"],
        },
    },
    "set_reminder": {
        "name": "set_reminder",
        "category": "accounting",
        "access": "user",
        "description": (
            "Use when a user wants a WhatsApp reminder sent to themselves at a future time. "
            "Self-only — cannot set reminders for other people. "
            "Returns: confirmation with the scheduled datetime."
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
    "list_reminders": {
        "name": "list_reminders",
        "category": "accounting",
        "access": "user",
        "description": (
            "Lists pending (not yet sent) reminders for the current user in this group. "
            "Shows each reminder's ID prefix, message text, and scheduled time. "
            "Returns: numbered list, or 'No pending reminders.'"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "cancel_reminder": {
        "name": "cancel_reminder",
        "category": "accounting",
        "access": "user",
        "description": (
            "Cancels a pending reminder by its ID prefix. "
            "Use the ID prefix shown by list_reminders (at least 4 characters). "
            "Returns: confirmation that the reminder was cancelled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "The reminder ID prefix shown by list_reminders (at least 4 characters).",
                },
            },
            "required": ["reminder_id"],
        },
    },
    "set_report_email": {
        "name": "set_report_email",
        "category": "accounting",
        "access": "user",
        "description": (
            "Saves the user's email address for PDF/XLSX report delivery. "
            "Use when a user says 'send reports to my email' or provides an address for export. "
            "Returns: confirmation that the email was saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to save"},
            },
            "required": ["email"],
        },
    },
    "rename_participant": {
        "name": "rename_participant",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Use when an admin wants to set or clear the display name for a group participant. Admin only. "
            "Pass empty string to revert to their WhatsApp push name. "
            "Returns: confirmation of the new display name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The participant's phone number (digits only)"},
                "name": {"type": "string", "description": "New display name, or empty string to clear override"},
            },
            "required": ["phone", "name"],
        },
    },
    "set_household": {
        "name": "set_household",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Use when an admin wants to mark or unmark a participant as part of the shared household account (shown as 'Parents'). Admin only. "
            "Returns: confirmation of the updated household status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The participant's phone number (digits only)"},
                "is_household": {"type": "boolean", "description": "True to add to household, False to remove"},
            },
            "required": ["phone", "is_household"],
        },
    },
    "list_participants": {
        "name": "list_participants",
        "category": "accounting",
        "access": "user",
        "description": (
            "Returns the list of active group members with their display names and phone numbers. "
            "Use this before calling get_balance, record_expense, rename_participant, or set_household "
            "when you need to resolve a name to a phone number. "
            "Returns: each participant's phone, display name, and household status."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "correct_transaction": {
        "name": "correct_transaction",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Step 1 of 2 — proposes a correction to an existing transaction. Admin only. "
            "Accepts partial updates: new date, new total amount in ILS, participants to add or remove. "
            "Returns a human-readable diff and a correction_token. "
            "You MUST then call commit_correction with that token to apply the change. "
            "Do not tell the user the change is applied until commit_correction succeeds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "Transaction ID prefix (at least 8 chars) to correct"},
                "new_date": {"type": "string", "description": "New date YYYY-MM-DD (optional)"},
                "new_amount_ils": {"type": "number", "description": "New total amount in ILS for the transaction (optional)"},
                "add_phones": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Phone numbers of participants to add (optional)",
                },
                "remove_phones": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Phone numbers of participants to remove (optional)",
                },
            },
            "required": ["transaction_id"],
        },
    },
    "create_report_format": {
        "name": "create_report_format",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Use when an admin wants to save a named report layout for XLSX exports. Admin only. "
            "Returns: confirmation that the format was saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Format name (e.g. 'monthly', 'detailed')"},
                "language": {"type": "string", "description": "'he' or 'en'", "enum": ["he", "en"]},
                "grouping": {"type": "string", "description": "How to group rows", "enum": ["person", "month", "none"]},
                "sections": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Sections to include: 'balances', 'transactions', 'settlements'",
                },
                "date_format": {"type": "string", "description": "'DD/MM/YYYY', 'YYYY-MM-DD', or 'DD MMM YYYY'"},
                "currency_display": {"type": "string", "description": "'ILS' or '₪'"},
                "include_settled": {"type": "boolean", "description": "Include fully-settled transaction legs"},
                "sort_by": {"type": "string", "description": "'date', 'person', or 'amount'", "enum": ["date", "person", "amount"]},
            },
            "required": ["name"],
        },
    },
    "list_report_formats": {
        "name": "list_report_formats",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Use when an admin asks to see saved report formats. Admin only. "
            "Returns: list of named formats with their settings."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "delete_report_format": {
        "name": "delete_report_format",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Use when an admin wants to delete a named report format. Admin only. "
            "Returns: confirmation that the format was deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Format name to delete"},
            },
            "required": ["name"],
        },
    },
    "commit_correction": {
        "name": "commit_correction",
        "category": "accounting",
        "access": "admin",
        "description": (
            "Step 2 of 2 — applies a staged transaction correction. Admin only. "
            "Only call this after calling correct_transaction and receiving a correction_token. "
            "Requires the token returned by correct_transaction. "
            "Returns confirmation that the correction was applied."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "The correction_token returned by correct_transaction."},
                "admin_phone": {"type": "string", "description": "Admin's phone number."},
            },
            "required": ["token"],
        },
    },
}


# ── Executors ─────────────────────────────────────────────────────────────────

async def _exec_record_transaction(params: dict, **ctx) -> str:
    from datetime import date as _date
    group_jid = ctx.get("group_jid", "")
    sender = ctx.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]

    payer_phone: str = params["payer_phone"]
    participant_phones: list[str] = params["participant_phones"]
    amount: float = params["amount"]
    currency: str = params.get("currency", "ILS")
    description: str = params.get("description", "")
    tx_date_str: str | None = params.get("transaction_date")
    transaction_date = (
        _date.fromisoformat(tx_date_str) if tx_date_str else _date.today()
    )

    if not participant_phones:
        return "Error: participant_phones must not be empty."

    with SessionLocal() as db:
        try:
            amount_ils = await to_ils(Decimal(str(amount)), currency, transaction_date)
        except RuntimeError as exc:
            return str(exc)

        if _account_service:
            # New cross-group routing path
            results = []
            for debtor_phone in participant_phones:
                msg = await _account_service.process_transaction(
                    db=db,
                    reporter_phone=sender_phone,
                    reporter_group_jid=group_jid,
                    payer_phone=payer_phone,
                    debtor_phone=debtor_phone,
                    amount_ils=amount_ils,
                    description=description,
                    transaction_date=transaction_date,
                )
                results.append(msg)
            return " ".join(results)

    # Legacy same-group path (other blueprints or when no AccountService)
    return await _legacy_record_transaction(params, **ctx)


async def _legacy_record_transaction(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    sender = ctx.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
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

    # Determine who needs to confirm:
    # - Each participant (debtor) must confirm unless they sent the message.
    # - If sender is not the payer, the payer must also confirm.
    awaiting: list[str] = []
    for phone in participants:
        if phone != sender_phone:
            awaiting.append(phone)
    if payer != sender_phone and payer not in awaiting:
        awaiting.append(payer)

    if not awaiting:
        # All parties are the sender — record immediately
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
            f"split equally {per_person:.2f} ILS each among {len(participants)}"
            if len(participants) > 1 else f"{amount_ils:.2f} ILS"
        )
        return f"Recorded: {payer} paid for {', '.join(participants)} — {split_info}. (tx: {transaction_id[:8]})"

    # Stage multi-party confirmation
    mcs = ctx.get("multi_confirmation_store")
    if mcs is None:
        return "Error: confirmation system unavailable."

    commit_params = {
        "group_jid": group_jid,
        "transaction_id": transaction_id,
        "payer_phone": payer,
        "participant_phones": participants,
        "per_person_ils": str(per_person),
        "description": desc_with_fx,
        "transaction_date": tx_date.isoformat(),
    }
    split_info = (
        f"{per_person:.2f} ILS each among {len(participants)} people"
        if len(participants) > 1 else f"{amount_ils:.2f} ILS"
    )
    pending_desc = (
        f"{payer} paid for {', '.join(participants)} — {split_info}\n"
        f"Description: {description}"
    )
    await mcs.propose(group_jid, awaiting, "commit_transaction", commit_params, pending_desc)
    return f"Confirmation requested from {len(awaiting)} participant(s)."


async def _exec_record_payment(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    sender = ctx.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
    payer = params["payer_phone"]
    payee = params["payee_phone"]
    amount_ils = Decimal(str(params["amount_ils"]))
    pay_date_str = params.get("payment_date") or date.today().isoformat()
    pay_date = date.fromisoformat(pay_date_str)

    if _account_service:
        # Cross-group routing path: confirmation goes to the payer's personal group
        # with a 24-hour TTL so they can respond at their own pace.
        with SessionLocal() as db:
            try:
                return await _account_service.process_payment(
                    db=db,
                    reporter_phone=sender_phone,
                    reporter_group_jid=group_jid,
                    payer_phone=payer,
                    payee_phone=payee,
                    amount_ils=amount_ils,
                    payment_date=pay_date,
                )
            except ValueError as exc:
                return f"Cannot process payment: {exc}"

    # Legacy same-group path (no AccountService configured)
    # Determine who needs to confirm:
    # - If sender is the payer → payee must confirm receiving.
    # - Otherwise → payer must confirm paying.
    if sender_phone == payer:
        awaiting = [payee]
    else:
        awaiting = [payer]

    mcs = ctx.get("multi_confirmation_store")
    if mcs is None:
        return "Error: confirmation system unavailable."

    commit_params = {
        "group_jid": group_jid,
        "payer_phone": payer,
        "payee_phone": payee,
        "amount_ils": str(amount_ils),
        "payment_date": pay_date.isoformat(),
    }
    pending_desc = f"Payment of {amount_ils:.2f} ILS from {payer} to {payee} on {pay_date.isoformat()}"
    await mcs.propose(group_jid, awaiting, "commit_payment", commit_params, pending_desc)
    confirmer = "payee" if sender_phone == payer else "payer"
    return f"Confirmation requested from {confirmer} ({awaiting[0]})."


async def _exec_get_balance(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    phone_a = params["phone_a"]
    phone_b = params.get("phone_b")

    # Non-admins may only query their own phone
    if not is_admin:
        phone_a = _sender_phone(ctx)
        phone_b = None

    def net_vs_group(db, group_jid, from_phone, to_phones):
        owes = sum(_net_owed(db, group_jid, from_phone, cp) for cp in to_phones)
        owed = sum(_net_owed(db, group_jid, cp, from_phone) for cp in to_phones)
        return owes - owed

    with SessionLocal() as db:
        household = _household_phones_from_db(db, group_jid)
        names = _phone_to_name_from_db(db, group_jid)

        def label(phone: str) -> str:
            return names.get(phone, phone)

        if phone_b:
            if phone_a in household and phone_b in household:
                return f"{label(phone_a)} and {label(phone_b)} share a household — no debt tracked between them."
            counterparts_a = household if phone_b in household else {phone_b}
            counterparts_b = household if phone_a in household else {phone_a}
            net = net_vs_group(db, group_jid, phone_a, counterparts_a) if phone_a not in household \
                else -net_vs_group(db, group_jid, phone_b, counterparts_b)
            la, lb = label(phone_a), label(phone_b)
            if net > Decimal("0"):
                return f"{la} owes {lb}: {net:.2f} ILS"
            elif net < Decimal("0"):
                return f"{lb} owes {la}: {(-net):.2f} ILS"
            return f"{la} and {lb} are settled up."

        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                or_(LedgerEntry.from_phone == phone_a, LedgerEntry.to_phone == phone_a),
            )
            .all()
        )
        all_partners = {r.from_phone if r.to_phone == phone_a else r.to_phone for r in rows}
        all_partners.discard(phone_a)
        if phone_a in household:
            all_partners -= household
        household_partners = all_partners & household
        individual_partners = sorted(all_partners - household)
        lines = []
        if household_partners and phone_a not in household:
            net = net_vs_group(db, group_jid, phone_a, household_partners)
            la = label(phone_a)
            if net > Decimal("0"):
                lines.append(f"{la} owes Parents: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"Parents owe {la}: {(-net):.2f} ILS")
        for partner in individual_partners:
            a_owes = _net_owed(db, group_jid, phone_a, partner)
            p_owes = _net_owed(db, group_jid, partner, phone_a)
            net = a_owes - p_owes
            la, lp = label(phone_a), label(partner)
            if net > Decimal("0"):
                lines.append(f"{la} owes {lp}: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"{lp} owes {la}: {(-net):.2f} ILS")
        return "\n".join(lines) if lines else f"No open balances for {label(phone_a)}."


async def _exec_get_debt_summary(params: dict, **ctx) -> str:
    from collections import defaultdict
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    sender_phone = _sender_phone(ctx)

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(
            LedgerEntry.group_jid == group_jid,
            LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
        )
        if not is_admin and sender_phone:
            q = q.filter(
                or_(
                    LedgerEntry.from_phone == sender_phone,
                    LedgerEntry.to_phone == sender_phone,
                )
            )
        rows = q.order_by(LedgerEntry.transaction_date).all()

    if not rows:
        return "No open debts." if is_admin else "You have no open debts."

    # Aggregate net per (debtor, creditor) pair
    net: dict = defaultdict(Decimal)
    oldest: dict = {}
    for r in rows:
        key = (r.from_phone, r.to_phone)
        net[key] += r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        if key not in oldest or r.transaction_date < oldest[key]:
            oldest[key] = r.transaction_date

    lines = []
    for (debtor, creditor), amount in sorted(net.items(), key=lambda x: -x[1]):
        if amount <= Decimal("0"):
            continue
        lines.append(
            f"{debtor} owes {creditor}: ₪{float(amount):,.2f} "
            f"(since {oldest[(debtor, creditor)]})"
        )
    if not lines:
        return "No open debts." if is_admin else "You have no open debts."
    return "\n".join(lines)


async def _exec_get_history(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    from_date = params.get("from_date")
    to_date = params.get("to_date")

    # Non-admins always see only their own transactions
    if is_admin:
        phone = params.get("phone")
    else:
        phone = _sender_phone(ctx)

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid)
        if phone:
            q = q.filter(or_(LedgerEntry.from_phone == phone, LedgerEntry.to_phone == phone))
        if from_date:
            q = q.filter(LedgerEntry.transaction_date >= date.fromisoformat(from_date))
        if to_date:
            q = q.filter(LedgerEntry.transaction_date <= date.fromisoformat(to_date))
        rows = q.order_by(LedgerEntry.transaction_date).all()
        names = _phone_to_name_from_db(db, group_jid)

    if not rows:
        return "No transactions found."

    lines = []
    for r in rows:
        remaining = r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        status = "settled" if remaining <= Decimal("0") else f"{remaining:.2f} ILS remaining"
        frm = names.get(r.from_phone, r.from_phone)
        to = names.get(r.to_phone, r.to_phone)
        lines.append(
            f"{r.transaction_date} | {frm} → {to} | "
            f"{r.amount_ils:.2f} ILS | {status} | {r.description}"
        )
    return "\n".join(lines)


async def _exec_get_transaction(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    if not is_admin:
        return "get_transaction is admin only."
    tx_prefix = params.get("transaction_id", "").strip()
    if len(tx_prefix) < 8:
        return "Please provide at least 8 characters of the transaction ID."
    with SessionLocal() as db:
        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.transaction_id.startswith(tx_prefix),
            )
            .all()
        )
    if not rows:
        return f"No transaction found matching '{tx_prefix}'."
    first = rows[0]
    total = sum(r.amount_ils for r in rows)
    settled_total = sum(r.amount_settled_ils or Decimal("0") for r in rows)
    lines = [
        f"Transaction: {first.transaction_id[:16]}",
        f"Date: {first.transaction_date}",
        f"Description: {first.description}",
        f"Total: ₪{float(total):,.2f} | Settled: ₪{float(settled_total):,.2f}",
        "Legs:",
    ]
    for r in rows:
        status = "settled" if (r.amount_settled_ils or Decimal("0")) >= r.amount_ils else "open"
        lines.append(f"  {r.from_phone} → {r.to_phone}: ₪{float(r.amount_ils):,.2f} [{status}]")
    return "\n".join(lines)


async def _exec_set_reminder(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    to_phone = _sender_phone(ctx)
    if not to_phone:
        return "Error: could not determine sender phone. Please try again."
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


async def _exec_list_reminders(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    to_phone = _sender_phone(ctx)
    if not to_phone:
        return "Error: could not determine sender phone."
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = (
            db.query(ScheduledMessage)
            .filter(
                ScheduledMessage.group_jid == group_jid,
                ScheduledMessage.to_phone == to_phone,
                ScheduledMessage.sent == False,
                ScheduledMessage.cancelled == False,
                ScheduledMessage.send_at > now,
            )
            .order_by(ScheduledMessage.send_at)
            .all()
        )
    if not rows:
        return "No pending reminders."
    lines = [
        f"{i+1}. [{r.id[:8]}] {r.send_at.strftime('%d/%m/%Y %H:%M')} UTC — {r.message}"
        for i, r in enumerate(rows)
    ]
    return "\n".join(lines)


async def _exec_cancel_reminder(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    to_phone = _sender_phone(ctx)
    if not to_phone:
        return "Error: could not determine sender phone."
    reminder_id_prefix = params.get("reminder_id", "").strip()
    if len(reminder_id_prefix) < 4:
        return "Please provide at least 4 characters of the reminder ID."
    with SessionLocal() as db:
        row = (
            db.query(ScheduledMessage)
            .filter(
                ScheduledMessage.group_jid == group_jid,
                ScheduledMessage.to_phone == to_phone,
                ScheduledMessage.id.startswith(reminder_id_prefix),
                ScheduledMessage.sent == False,
                ScheduledMessage.cancelled == False,
            )
            .first()
        )
        if row is None:
            return f"No pending reminder found matching '{reminder_id_prefix}'."
        row.cancelled = True
        db.commit()
        msg = row.message
    return f"Reminder cancelled: \"{msg}\""


async def _exec_save_email(params: dict, **ctx) -> str:
    phone = _sender_phone(ctx)
    if not phone:
        return "Error: could not determine your phone number."
    email = params["email"].strip()
    if not email or "@" not in email:
        return "Invalid email address."

    with SessionLocal() as db:
        profile = db.get(UserProfile, phone)
        if profile is None:
            db.add(UserProfile(phone=phone, email=email))
        else:
            profile.email = email
        db.commit()

    return f"Email saved: {email}"


async def _exec_rename_participant(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can rename participants."
    group_jid = ctx.get("group_jid", "")
    phone = params["phone"]
    name = params["name"].strip()

    db = ctx.get("db")
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        from app.db.models import GroupParticipant
        row = db.get(GroupParticipant, (group_jid, phone))
        if row is None:
            return f"Participant {phone} not found in this group."
        row.admin_name = name or None
        db.commit()
        display = name or row.push_name or phone
        if name:
            return f"Renamed {phone} to \"{display}\"."
        return f"Display name cleared for {phone} — reverted to WhatsApp name."
    finally:
        if close_db:
            db.close()


async def _exec_set_household(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can change household membership."
    group_jid = ctx.get("group_jid", "")
    phone = params["phone"]
    is_household = params["is_household"]

    db = ctx.get("db")
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        from app.db.models import GroupParticipant
        row = db.get(GroupParticipant, (group_jid, phone))
        if row is None:
            return f"Participant {phone} not found in this group."
        row.is_household = is_household
        db.commit()
        name = row.admin_name or row.push_name or phone
        action = "added to" if is_household else "removed from"
        return f"{name} {action} the shared household account."
    finally:
        if close_db:
            db.close()


async def _exec_list_participants(params: dict, **ctx) -> str:
    from app.db.models import GroupParticipant
    group_jid = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rows = (
            db.query(GroupParticipant)
            .filter(
                GroupParticipant.group_jid == group_jid,
                GroupParticipant.status == "active",
            )
            .order_by(GroupParticipant.phone)
            .all()
        )
    if not rows:
        return "No participants found."
    lines = []
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        household = " [household]" if r.is_household else ""
        lines.append(f"{r.phone} — {name}{household}")
    return "\n".join(lines)


async def _exec_correct_transaction(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can correct transactions."
    group_jid = ctx.get("group_jid", "")
    admin_phone = _sender_phone(ctx)
    tx_prefix = params["transaction_id"]

    new_date = params.get("new_date")
    new_amount_ils = params.get("new_amount_ils")
    add_phones: list[str] = params.get("add_phones") or []
    remove_phones: list[str] = params.get("remove_phones") or []

    if not any([new_date, new_amount_ils is not None, add_phones, remove_phones]):
        return "No changes specified. Provide at least one of: new_date, new_amount_ils, add_phones, remove_phones."

    with SessionLocal() as db:
        legs = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.transaction_id.like(f"{tx_prefix}%"),
            )
            .all()
        )
        if not legs:
            return f"No transaction found with ID starting '{tx_prefix}'."
        if len({leg.transaction_id for leg in legs}) > 1:
            return f"Prefix '{tx_prefix}' matches multiple transactions. Use a longer prefix."

        transaction_id = legs[0].transaction_id
        payer = legs[0].to_phone

        # Block if any removed participant has settlements
        if remove_phones:
            for leg in legs:
                if leg.from_phone in remove_phones:
                    settled = leg.amount_settled_ils or Decimal("0")
                    if settled > Decimal("0"):
                        return (
                            f"Cannot remove {leg.from_phone}: they have {settled:.2f} ILS already settled "
                            f"on this transaction. Clear the payment first."
                        )

        num_admins = _count_admins(db)

    # Build human-readable diff
    diff_lines = [f"Correction to transaction {transaction_id[:8]}:"]
    current_legs = {leg.from_phone: leg for leg in legs}
    current_participants = sorted(current_legs.keys())

    if new_date:
        diff_lines.append(f"  Date: {legs[0].transaction_date} → {new_date}")
    if new_amount_ils is not None:
        total_current = sum(leg.amount_ils for leg in legs)
        diff_lines.append(f"  Total amount: {total_current:.2f} ILS → {new_amount_ils:.2f} ILS")
    if add_phones:
        diff_lines.append(f"  Add participants: {', '.join(add_phones)}")
    if remove_phones:
        diff_lines.append(f"  Remove participants: {', '.join(remove_phones)}")

    changes = {
        "transaction_id": transaction_id,
        "payer": payer,
        "new_date": new_date,
        "new_amount_ils": new_amount_ils,
        "add_phones": add_phones,
        "remove_phones": remove_phones,
        "current_participants": current_participants,
    }

    result = correction_queue.enqueue(
        group_jid=group_jid,
        admin_phone=admin_phone,
        transaction_id=transaction_id,
        changes=changes,
        description="\n".join(diff_lines),
        max_slots=num_admins + 1,
    )
    if isinstance(result, str):
        return result  # error message

    # Also set confirmation_store so "yes"/"confirm" applies it
    confirmation_store = ctx.get("confirmation_store")
    if confirmation_store:
        if not confirmation_store.set(
            group_jid,
            "commit_correction",
            {"token": result.token, "admin_phone": admin_phone},
            "\n".join(diff_lines),
        ):
            return "⚠️ Another action is already pending for this group. Please reply 'yes' to confirm or 'no' to cancel it before requesting a new action."

    return "\n".join(diff_lines) + f"\n\nToken: {result.token}\nReply 'confirm' to apply or 'cancel' to discard."


async def _exec_apply_correction(params: dict, **ctx) -> str:
    """Internal tool called by the confirmation flow when admin says 'yes'."""
    group_jid = ctx.get("group_jid", "")
    token = params.get("token", "")
    admin_phone = params.get("admin_phone", "") or _sender_phone(ctx)

    correction = correction_queue.get_by_token(group_jid, token)
    if correction is None:
        return "Correction not found or expired."
    if correction.admin_phone != admin_phone:
        return "You can only confirm your own pending corrections."

    changes = correction.changes
    transaction_id = changes["transaction_id"]
    payer = changes["payer"]

    with SessionLocal() as db:
        legs = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.transaction_id == transaction_id,
            )
            .all()
        )
        if not legs:
            correction_queue.remove(group_jid, token)
            return "Transaction no longer exists."

        current_legs = {leg.from_phone: leg for leg in legs}
        current_participants = list(current_legs.keys())

        # Compute new per-person amount
        remove_phones: list[str] = changes.get("remove_phones") or []
        add_phones: list[str] = changes.get("add_phones") or []
        new_participants = [p for p in current_participants if p not in remove_phones] + add_phones

        if not new_participants:
            db.rollback()
            correction_queue.remove(group_jid, token)
            return "Error: correction would remove all participants."

        if changes.get("new_amount_ils") is not None:
            total_ils = Decimal(str(changes["new_amount_ils"]))
        else:
            total_ils = sum(leg.amount_ils for leg in legs)

        per_person = (total_ils / Decimal(len(new_participants))).quantize(Decimal("0.01"))
        new_date_val = date.fromisoformat(changes["new_date"]) if changes.get("new_date") else None

        # Remove legs for removed participants
        for phone in remove_phones:
            if phone in current_legs:
                db.delete(current_legs[phone])

        # Update existing legs
        for phone, leg in current_legs.items():
            if phone in remove_phones:
                continue
            if new_date_val:
                leg.transaction_date = new_date_val
            if changes.get("new_amount_ils") is not None:
                leg.amount_ils = per_person

        # Add new legs
        now = datetime.now(timezone.utc)
        for phone in add_phones:
            tx_date = new_date_val or legs[0].transaction_date
            db.add(LedgerEntry(
                transaction_id=transaction_id,
                group_jid=group_jid,
                from_phone=phone,
                to_phone=payer,
                amount_ils=per_person,
                amount_settled_ils=Decimal("0"),
                description=legs[0].description,
                transaction_date=tx_date,
                created_at=now,
            ))

        db.commit()

    correction_queue.remove(group_jid, token)
    return f"Correction applied to transaction {transaction_id[:8]}."


async def _exec_create_report_format(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can manage report formats."
    group_jid = ctx.get("group_jid", "")
    name = params["name"].strip()
    if not name:
        return "Format name cannot be empty."

    config = {
        "language": params.get("language", "en"),
        "grouping": params.get("grouping", "none"),
        "sections": params.get("sections") or ["balances", "transactions"],
        "date_format": params.get("date_format", "YYYY-MM-DD"),
        "currency_display": params.get("currency_display", "ILS"),
        "include_settled": params.get("include_settled", True),
        "sort_by": params.get("sort_by", "date"),
    }

    with SessionLocal() as db:
        existing = db.query(ReportFormat).filter_by(group_jid=group_jid, name=name).first()
        if existing:
            existing.config_json = json.dumps(config)
        else:
            db.add(ReportFormat(group_jid=group_jid, name=name, config_json=json.dumps(config)))
        db.commit()

    return f"Report format '{name}' saved."


async def _exec_list_report_formats(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can view report formats."
    group_jid = ctx.get("group_jid", "")

    with SessionLocal() as db:
        rows = db.query(ReportFormat).filter_by(group_jid=group_jid).all()

    if not rows:
        return "No report formats saved for this group."

    lines = []
    for r in rows:
        cfg = r.config()
        lines.append(
            f"• {r.name}: lang={cfg.get('language','en')}, "
            f"grouping={cfg.get('grouping','none')}, "
            f"sections={cfg.get('sections')}, "
            f"sort={cfg.get('sort_by','date')}"
        )
    return "Report formats:\n" + "\n".join(lines)


async def _exec_delete_report_format(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can delete report formats."
    group_jid = ctx.get("group_jid", "")
    name = params["name"].strip()

    with SessionLocal() as db:
        deleted = db.query(ReportFormat).filter_by(group_jid=group_jid, name=name).delete()
        db.commit()

    if deleted:
        return f"Report format '{name}' deleted."
    return f"Report format '{name}' not found."


# ── Public factory ─────────────────────────────────────────────────────────────

def get_accounting_tools() -> dict[str, dict]:
    """Return all accounting tools in ToolRegistry format."""
    return {
        name: {"schema": _SCHEMAS[name], "executor": executor}
        for name, executor in [
            ("record_expense",       _exec_record_transaction),
            ("record_payment",       _exec_record_payment),
            ("get_balance",          _exec_get_balance),
            ("get_debt_summary",     _exec_get_debt_summary),
            ("get_history",          _exec_get_history),
            ("get_transaction",      _exec_get_transaction),
            ("set_reminder",         _exec_set_reminder),
            ("list_reminders",       _exec_list_reminders),
            ("cancel_reminder",      _exec_cancel_reminder),
            ("set_report_email",     _exec_save_email),
            ("rename_participant",   _exec_rename_participant),
            ("set_household",        _exec_set_household),
            ("list_participants",    _exec_list_participants),
            ("correct_transaction",  _exec_correct_transaction),
            ("commit_correction",    _exec_apply_correction),
            ("create_report_format", _exec_create_report_format),
            ("list_report_formats",  _exec_list_report_formats),
            ("delete_report_format", _exec_delete_report_format),
        ]
    }
