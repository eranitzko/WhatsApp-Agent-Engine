"""Split-bill tool for the personal accounting blueprint."""

from __future__ import annotations

import logging
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from app.db.session import SessionLocal
from app.tools.accounting_fx import to_ils

if TYPE_CHECKING:
    from app.accounting.account_service import AccountService

logger = logging.getLogger(__name__)

_account_service: "AccountService | None" = None


def set_account_service(service: "AccountService | None") -> None:
    global _account_service
    _account_service = service


_SCHEMA = {
    "name": "record_split",
    "description": (
        "Record a split bill where one person paid and the cost is shared. "
        "The payer's own share is absorbed. All other participants either confirm "
        "their share (2nd-party) or self-report it (1st-party if reporter is a participant). "
        "Amounts default to equal split; pass custom_shares to override per-person amounts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "payer_phone": {"type": "string", "description": "Phone of the person who paid the full bill"},
            "all_phones": {
                "type": "array",
                "items": {"type": "string"},
                "description": "All participants including the payer",
            },
            "amount": {"type": "number", "description": "Total bill amount"},
            "currency": {"type": "string", "description": "ISO 4217, e.g. ILS, USD"},
            "description": {"type": "string", "description": "What the bill was for"},
            "custom_shares": {
                "type": "object",
                "description": (
                    "Optional per-phone override amounts (excluding payer). "
                    "Unspecified participants split the remainder equally. "
                    'Example: {"972501": 80, "972502": 50}'
                ),
            },
            "transaction_date": {
                "type": "string",
                "description": "Date YYYY-MM-DD; defaults to today",
            },
        },
        "required": ["payer_phone", "all_phones", "amount", "currency", "description"],
    },
}


async def _execute_record_split(params: dict, **kwargs) -> str:
    sender = kwargs.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
    group_jid = kwargs.get("group_jid", "")

    payer_phone: str = params["payer_phone"]
    all_phones: list[str] = params["all_phones"]
    amount: float = params["amount"]
    currency: str = params.get("currency", "ILS")
    description: str = params.get("description", "")
    custom_shares: dict = params.get("custom_shares") or {}
    tx_date_str: str | None = params.get("transaction_date")
    transaction_date = _date.fromisoformat(tx_date_str) if tx_date_str else _date.today()

    non_payer_phones = [p for p in all_phones if p != payer_phone]
    if not non_payer_phones:
        return "No participants to split with (all phones are the payer)."

    total_participants = len(all_phones)

    with SessionLocal() as db:
        total_ils = await to_ils(Decimal(str(amount)), currency, transaction_date)

        shares = _compute_shares(total_ils, non_payer_phones, custom_shares, total_participants)

        if _account_service is None:
            return "AccountService not configured — split bill unavailable."

        split = await _account_service.process_split(
            db=db,
            reporter_phone=sender_phone,
            reporter_group_jid=group_jid,
            payer_phone=payer_phone,
            shares=shares,
            total_amount=total_ils,
            description=description,
            transaction_date=transaction_date,
        )

    share_summary = ", ".join(
        f"₪{float(s['amount_ils']):.2f} → {s['phone']}" for s in shares
    )
    return (
        f"Split bill created (₪{float(total_ils):.2f} {description}). "
        f"Shares: {share_summary}. "
        f"Waiting for confirmations — all must confirm for the split to be recorded."
    )


def _compute_shares(
    total: Decimal,
    non_payer_phones: list[str],
    custom_shares: dict,
    total_participants: int | None = None,
) -> list[dict]:
    specified = {p: Decimal(str(v)) for p, v in custom_shares.items() if p in non_payer_phones}
    specified_total = sum(specified.values(), Decimal("0"))

    unspecified = [p for p in non_payer_phones if p not in specified]

    if unspecified:
        if total_participants and total_participants > len(non_payer_phones):
            # Equal split across all participants; payer absorbs own share
            per_person = (total / total_participants).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            remaining = total - specified_total
            per_person = (remaining / len(unspecified)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
    else:
        per_person = Decimal("0")

    shares = []
    for phone in non_payer_phones:
        if phone in specified:
            shares.append({"phone": phone, "amount_ils": specified[phone]})
        else:
            shares.append({"phone": phone, "amount_ils": per_person})

    return shares


def get_split_tools() -> dict:
    return {
        "record_split": {
            "schema": _SCHEMA,
            "executor": _execute_record_split,
        }
    }
