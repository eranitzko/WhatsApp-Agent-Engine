"""Tool definitions (Claude format) and their async executors.

Tools exposed to Claude:
  get_status, list_invoices, get_invoice_summary, update_config,
  save_invoice, flag_invoice, unflag_invoice, set_invoice_date, set_invoice_amount,
  add_date_format, stage_action

remove_invoice and send_report_by_email are NOT exposed as tools.
They execute only via the confirmation callback.

Admin enforcement happens here in code, independent of Claude's reasoning.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import GroupConfig, Invoice
from app.db.session import SessionLocal
from app.utils.date_formats import parse_format_string

logger = logging.getLogger(__name__)

# ── Tool schemas (Claude tool_use format) ────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_status",
        "description": (
            "Returns group configuration: language setting, report header, "
            "report author, and dual-currency flag. "
            "For invoice statistics, use get_invoice_summary instead."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "access": "user",
    },
    {
        "name": "list_invoices",
        "description": (
            "Use when a user wants to see the list of invoices for a month. "
            "Returns: each invoice's ID, vendor, date, original amount, ILS amount, and flag status. "
            "Defaults to the current month. Available to all members."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "Month number 1–12. Defaults to current month."},
                "year":  {"type": "integer", "description": "4-digit year. Defaults to current year."},
            },
            "required": [],
        },
        "access": "user",
    },
    {
        "name": "get_invoice_summary",
        "description": (
            "Returns invoice statistics for a month: count, total ILS, and number of flagged invoices. "
            "Defaults to the current month. "
            "For group settings, use get_status instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "Month number 1–12. Defaults to current month."},
                "year":  {"type": "integer", "description": "4-digit year. Defaults to current year."},
            },
            "required": [],
        },
        "access": "user",
    },
    {
        "name": "update_config",
        "description": (
            "Use when an admin wants to change a group setting. Admin only. "
            "Keys: 'header' (report title), 'author' (report author), 'language' (en or he), 'dual-currency' (on or off). "
            "Returns: confirmation of the updated setting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key":   {"type": "string", "enum": ["header", "author", "language", "dual-currency"]},
                "value": {"type": "string", "description": "New value for the setting."},
            },
            "required": ["key", "value"],
        },
        "access": "admin",
    },
    {
        "name": "flag_invoice",
        "description": (
            "Use when an admin wants to mark an invoice for manual review (e.g. suspicious amount, wrong vendor). Admin only. "
            "Returns: confirmation that the invoice is now flagged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to flag."},
                "reason":     {"type": "string", "description": "Reason for flagging. Optional."},
            },
            "required": ["invoice_id"],
        },
        "access": "admin",
    },
    {
        "name": "unflag_invoice",
        "description": (
            "Use when an admin wants to clear the review flag from an invoice. Admin only. "
            "Returns: confirmation that the flag is removed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to unflag."},
            },
            "required": ["invoice_id"],
        },
        "access": "admin",
    },
    {
        "name": "set_invoice_date",
        "description": (
            "Use when an admin reports that an invoice's date was extracted incorrectly (e.g. day/month swapped). Admin only. "
            "Call list_invoices first in this turn if you don't already have this invoice's UUID — never fall back to "
            "save_invoice to fix an existing invoice, that creates a duplicate. Prefer this over deletion when only the date is wrong. "
            "Also recalculates the ILS amount if the currency is not ILS. "
            "Returns: confirmation of the corrected date and recalculated amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to update."},
                "new_date":   {"type": "string", "description": "Correct date in YYYY-MM-DD format."},
            },
            "required": ["invoice_id", "new_date"],
        },
        "access": "admin",
    },
    {
        "name": "set_invoice_amount",
        "description": (
            "Corrects an invoice's extracted amount. Admin only. "
            "Requires prior approval: call stage_action with action='set_invoice_amount' first "
            "and wait for the user to confirm before calling this tool. "
            "Recalculates the ILS amount using the stored exchange rate for the invoice's date. "
            "Returns: confirmation of the corrected amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to update."},
                "new_amount":  {
                    "type": "number",
                    "description": (
                        "Correct amount in the invoice's original currency. Cannot be zero. "
                        "May be negative for a refund/return/credit."
                    ),
                },
            },
            "required": ["invoice_id", "new_amount"],
        },
        "access": "admin",
    },
    {
        "name": "add_date_format",
        "description": (
            "Registers a new date format for invoice date parsing (e.g. MM/DD/YYYY). Admin only. "
            "Requires prior approval: call stage_action with action='add_date_format' first "
            "and wait for the user to confirm before calling this tool. "
            "Adds to existing formats without replacing them. "
            "Returns: confirmation of the added format."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "format_string": {
                    "type": "string",
                    "description": (
                        "Date format using D (day), M (month), Y (year) and one separator "
                        "(/, -, ., or space). Examples: MM/DD/YYYY, DD.MM.YY, YYYY-MM-DD"
                    ),
                },
            },
            "required": ["format_string"],
        },
        "access": "admin",
    },
    {
        "name": "save_invoice",
        "description": (
            "Manually save a NEW invoice entered as text (no image). Admin only. "
            "Use when a user TYPES invoice details as a request to record them — never in response to a "
            "'New invoice received' message, which is the system confirming an image was already processed "
            "and saved automatically; calling this on that message creates a duplicate of it. "
            "Never use this to correct or replace an existing invoice either — that also creates a duplicate "
            "row instead of fixing the original; use set_invoice_date or set_invoice_amount instead. "
            "Automatically rejects a save with the same amount+currency+date as an existing invoice, "
            "regardless of vendor (returns duplicate=true) — relay that to the user instead of retrying. "
            "Converts non-ILS amounts to ILS automatically using the Bank of Israel rate for the invoice date. "
            "Returns: the saved invoice ID and ILS amount."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor":          {"type": "string", "description": "Vendor or supplier name."},
                "amount":          {
                    "type": "number",
                    "description": (
                        "Invoice amount in the original currency. Cannot be zero. "
                        "May be negative for a refund/return/credit."
                    ),
                },
                "currency":        {"type": "string", "description": "ISO 4217 currency code (e.g. ILS, USD, EUR)."},
                "date":            {"type": "string", "description": "Invoice date in YYYY-MM-DD format. Defaults to today if omitted."},
                "invoice_number":  {"type": "string", "description": "Invoice or receipt number. Optional."},
                "description":     {"type": "string", "description": "Optional description or notes."},
            },
            "required": ["vendor", "amount", "currency"],
        },
        "access": "admin",
    },
    {
        "name": "stage_action",
        "description": (
            "Stages a destructive or external action for user approval. "
            "Use before: removing an invoice, correcting its amount, adding a date format, "
            "or sending anything outside the group. "
            "Tell the user what will happen and ask them to reply yes. "
            "The action only executes when the user confirms. "
            "Returns: a confirmation prompt to relay to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action":      {"type": "string", "enum": ["remove_invoice", "send_email", "set_invoice_amount", "add_date_format"], "description": "Action to confirm."},
                "params": {
                    "type": "object",
                    "description": (
                        "Parameters for the action. "
                        "remove_invoice: {invoice_id}. "
                        "send_email: {to, month?, year?, start_date?, end_date?, format, attach_images, dual_currency?}. "
                        "set_invoice_amount: {invoice_id, new_amount}. "
                        "add_date_format: {format_string}. "
                        "Note: use key 'to' (not 'to_email') for the recipient address."
                    ),
                },
                "description": {"type": "string", "description": "Short label identifying what will be removed or sent (e.g. vendor, date, amount). No warnings or caveats."},
            },
            "required": ["action", "params", "description"],
        },
        "access": "admin",
    },
]

# Mark the last tool for Claude prompt caching (caches all tool schemas up to this point)
TOOL_SCHEMAS[-1] = {**TOOL_SCHEMAS[-1], "cache_control": {"type": "ephemeral"}}


# ── Helpers ───────────────────────────────────────────────────────────────────

_TZ_IL = ZoneInfo("Asia/Jerusalem")


def _current_month_year() -> tuple[int, int]:
    """Return the current month and year in Israel time."""
    now = datetime.now(_TZ_IL)
    return now.month, now.year


def _get_or_create_config(db: Session, group_id: str) -> GroupConfig:
    config = db.get(GroupConfig, group_id)
    if not config:
        config = GroupConfig(group_id=group_id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


# ── Executors ─────────────────────────────────────────────────────────────────

async def exec_get_status(group_id: str, **_) -> dict:
    with SessionLocal() as db:
        config = _get_or_create_config(db, group_id)
        # Use Israel timezone so "this month" matches local calendar months
        now_il = datetime.now(_TZ_IL)
        month_start = now_il.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_count = db.query(Invoice).filter(Invoice.group_id == group_id, Invoice.received_at >= month_start).count()
        total_count = db.query(Invoice).filter(Invoice.group_id == group_id).count()
        flagged_count = db.query(Invoice).filter(Invoice.group_id == group_id, Invoice.flagged == True).count()

        from app.db.models import SystemConfig
        fmt_row = db.get(SystemConfig, "extra_date_formats")
        extra_formats = [f.strip() for f in fmt_row.value.split(",") if f.strip()] if fmt_row and fmt_row.value.strip() else []

    return {
        "language": config.feedback_language,
        "lead_currency": config.lead_currency,
        "dual_currency": "on" if config.force_dual_currency else "off (auto)",
        "report_header": config.report_header or "(not set)",
        "report_author": config.report_author or "(not set)",
        "invoices_this_month": month_count,
        "invoices_total": total_count,
        "invoices_flagged": flagged_count,
        "extra_date_formats": extra_formats,
    }


async def exec_list_invoices(group_id: str, month: int = None, year: int = None, **_) -> dict:
    cur_month, cur_year = _current_month_year()
    month = month or cur_month
    year  = year  or cur_year

    month_start = date(year, month, 1)
    month_end   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    with SessionLocal() as db:
        matched = (
            db.query(Invoice)
            .filter(
                Invoice.group_id == group_id,
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < month_end,
            )
            .order_by(Invoice.invoice_date, Invoice.created_at)
            .all()
        )

    if not matched:
        return {"invoices": [], "message": f"No invoices found for {month:02d}/{year}."}

    return {
        "month": month,
        "year": year,
        "count": len(matched),
        "invoices": [
            {
                "id": r.id,
                "date": str(r.invoice_date),
                "invoice_number": r.invoice_number or "—",
                "vendor": r.vendor or "—",
                "amount": f"{r.amount_original} {(r.currency_original or '').strip()}".strip(),
                "amount_ils": float(r.amount_ils) if r.amount_ils else None,
                "flagged": r.flagged,
                "flag_reason": r.flag_reason,
            }
            for r in matched
        ],
    }


async def exec_get_preview(group_id: str, month: int = None, year: int = None, **_) -> dict:
    cur_month, cur_year = _current_month_year()
    month = month or cur_month
    year  = year  or cur_year

    month_start = date(year, month, 1)
    month_end   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    with SessionLocal() as db:
        matched = (
            db.query(Invoice)
            .filter(
                Invoice.group_id == group_id,
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date < month_end,
            )
            .all()
        )

    total_ils = sum(float(r.amount_ils) for r in matched if r.amount_ils)
    flagged   = sum(1 for r in matched if r.flagged)
    currencies = {r.currency_original for r in matched if r.currency_original}

    return {
        "month": month,
        "year": year,
        "count": len(matched),
        "total_ils": round(total_ils, 2),
        "flagged": flagged,
        "currencies": sorted(currencies),
    }


async def exec_update_config(group_id: str, is_admin: bool, key: str, value: str, **_) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    valid = {"header", "author", "language", "dual-currency"}
    if key not in valid:
        return {"error": f"Unknown key '{key}'. Valid keys: {', '.join(valid)}"}

    with SessionLocal() as db:
        config = _get_or_create_config(db, group_id)

        if key == "language":
            if value not in ("en", "he"):
                return {"error": "Language must be 'en' or 'he'."}
            config.feedback_language = value

        elif key == "dual-currency":
            if value not in ("on", "off"):
                return {"error": "Value must be 'on' or 'off'."}
            config.force_dual_currency = (value == "on")

        elif key == "header":
            config.report_header = value

        elif key == "author":
            config.report_author = value

        db.commit()

    return {"ok": True, "key": key, "value": value}


async def exec_generate_report(
    group_id: str, is_admin: bool,
    month: int = None, year: int = None,
    start_date: str = None, end_date: str = None,
    format: str = "pdf", attach_images: bool = False, dual_currency: bool = None,
    **_,
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    from app.reports.generator import generate_and_send_report
    cur_month, cur_year = _current_month_year()
    return await generate_and_send_report(
        group_id=group_id,
        month=month or cur_month,
        year=year or cur_year,
        fmt=format or "pdf",
        attach_images=bool(attach_images),
        force_dual_currency=dual_currency,
        start_date=start_date,
        end_date=end_date,
    )


async def exec_flag_invoice(group_id: str, is_admin: bool, invoice_id: str, reason: str = "", **_) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    from app.pipeline.storage import sync_invoice_sidecar
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": f"Invoice {invoice_id} not found."}
        invoice.flagged = True
        invoice.flag_reason = reason or "Manually flagged"
        db.commit()
        db.refresh(invoice)
        await sync_invoice_sidecar(invoice)

    return {"ok": True, "invoice_id": invoice_id, "reason": reason}


async def exec_unflag_invoice(group_id: str, is_admin: bool, invoice_id: str, **_) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    from app.pipeline.storage import sync_invoice_sidecar
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": f"Invoice {invoice_id} not found."}
        invoice.flagged = False
        invoice.flag_reason = None
        db.commit()
        db.refresh(invoice)
        await sync_invoice_sidecar(invoice)

    return {"ok": True, "invoice_id": invoice_id}


async def exec_set_invoice_date(group_id: str, is_admin: bool, invoice_id: str, new_date: str, **_) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    try:
        from datetime import date as _date
        parsed_date = _date.fromisoformat(new_date)
    except ValueError:
        return {"error": f"Invalid date format '{new_date}'. Use YYYY-MM-DD."}

    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": f"Invoice not found."}

        invoice.invoice_date = parsed_date

        # Re-run currency conversion if not ILS
        if invoice.currency_original and invoice.currency_original != "ILS" and invoice.amount_original:
            from app.pipeline.converter import convert_to_ils
            from decimal import Decimal
            conversion = await convert_to_ils(
                Decimal(str(invoice.amount_original)), invoice.currency_original, parsed_date
            )
            if not conversion.error:
                invoice.amount_ils    = conversion.amount_ils
                invoice.exchange_rate = conversion.exchange_rate
                invoice.rate_source   = conversion.rate_source
                invoice.rate_date     = conversion.rate_date

        db.commit()
        db.refresh(invoice)
        from app.pipeline.storage import sync_invoice_sidecar
        await sync_invoice_sidecar(invoice)

    return {"ok": True, "invoice_id": invoice_id, "new_date": new_date}


async def exec_set_invoice_amount(
    group_id: str, is_admin: bool, invoice_id: str, new_amount: float, **_
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    from app.utils.invoice_amount import validate_invoice_amount
    validated = validate_invoice_amount(new_amount)
    if validated.error:
        return {"error": validated.error}
    amount = validated.amount

    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": "Invoice not found."}

        invoice.amount_original = amount

        if invoice.currency_original and invoice.currency_original != "ILS":
            from app.pipeline.converter import convert_to_ils
            conversion = await convert_to_ils(amount, invoice.currency_original, invoice.invoice_date)
            if not conversion.error:
                invoice.amount_ils    = conversion.amount_ils
                invoice.exchange_rate = conversion.exchange_rate
                invoice.rate_source   = conversion.rate_source
                invoice.rate_date     = conversion.rate_date
        else:
            invoice.amount_ils = amount

        db.commit()
        db.refresh(invoice)
        from app.pipeline.storage import sync_invoice_sidecar
        await sync_invoice_sidecar(invoice)

    return {
        "ok": True,
        "invoice_id": invoice_id,
        "new_amount": str(amount),
        "amount_ils": str(invoice.amount_ils),
    }


async def exec_add_date_format(
    group_id: str, is_admin: bool, format_string: str, **_
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    parsed = parse_format_string(format_string)
    if not parsed:
        return {
            "error": (
                f"Invalid format string '{format_string}'. "
                "Use D (day), M (month), Y (year) with a separator (/, -, ., or space). "
                "Example: MM/DD/YYYY or DD.MM.YY"
            )
        }

    normalized = parsed["normalized"]

    from app.db.models import SystemConfig
    with SessionLocal() as db:
        row = db.get(SystemConfig, "extra_date_formats")
        if not row:
            row = SystemConfig(key="extra_date_formats", value="")
            db.add(row)

        existing = [f.strip() for f in row.value.split(",") if f.strip()]
        if normalized in existing:
            return {"ok": True, "note": "already registered", "all_formats": existing}

        existing.append(normalized)
        row.value = ",".join(existing)
        db.commit()

    return {"ok": True, "format_string": normalized, "all_formats": existing}


async def exec_save_invoice(
    group_id: str, is_admin: bool,
    vendor: str, amount: float, currency: str,
    date: str = "", invoice_number: str = "", description: str = "",
    sender: str = "",
    **_,
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    from app.pipeline.converter import convert_to_ils
    from app.utils.invoice_amount import validate_invoice_amount

    validated = validate_invoice_amount(amount)
    if validated.error:
        return {"error": validated.error}
    amount_decimal = validated.amount

    currency = currency.strip().upper()
    if len(currency) != 3:
        return {"error": f"Invalid currency code: {currency!r}. Use an ISO 4217 code like ILS, USD, EUR."}

    # Parse date or default to today
    invoice_date = None
    if date:
        try:
            invoice_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Invalid date format: {date!r}. Use YYYY-MM-DD."}

    # Dedup: unlike the image pipeline, this path had no duplicate check at
    # all — the agent would sometimes call this redundantly right after an
    # image was already successfully processed (e.g. misreading the
    # pipeline's own "New invoice received" notification as a request to
    # save it again), silently creating a duplicate row every time.
    from app.pipeline.dedup import check_amount_date_duplicate
    existing = check_amount_date_duplicate(group_id, amount_decimal, currency, invoice_date)
    if existing:
        return {
            "duplicate": True,
            "duplicate_reason": "same_amount_and_date",
            "existing_invoice_id": existing.id,
            "existing_vendor": existing.vendor,
            "existing_date": str(existing.invoice_date) if existing.invoice_date else None,
            "existing_amount": (
                f"{existing.amount_original} {existing.currency_original}"
                if existing.amount_original else None
            ),
        }

    # Convert to ILS
    conversion = await convert_to_ils(amount_decimal, currency, invoice_date)
    flagged = bool(conversion.error)
    flag_reason = conversion.error or None

    invoice_id = str(uuid.uuid4())
    # Unique hash for manually entered invoices (no image)
    image_hash = hashlib.sha256(f"manual:{group_id}:{invoice_id}".encode()).hexdigest()

    invoice = Invoice(
        id=invoice_id,
        group_id=group_id,
        message_id=f"manual-{invoice_id}",
        image_hash=image_hash,
        r2_key=None,
        received_at=datetime.now(timezone.utc),
        invoice_date=invoice_date,
        invoice_number=invoice_number or None,
        vendor=vendor,
        description=description or None,
        amount_original=amount_decimal,
        currency_original=currency,
        amount_ils=conversion.amount_ils,
        exchange_rate=conversion.exchange_rate,
        rate_source=conversion.rate_source,
        rate_date=conversion.rate_date,
        extraction_confidence=None,
        flagged=flagged,
        flag_reason=flag_reason,
        submitted_by=sender or None,
    )

    with SessionLocal() as db:
        db.add(invoice)
        db.commit()

    result: dict = {
        "ok": True,
        "invoice_id": invoice_id,
        "vendor": vendor,
        "amount_original": str(amount_decimal),
        "currency": currency,
        "amount_ils": str(conversion.amount_ils) if conversion.amount_ils else None,
        "invoice_date": str(invoice_date) if invoice_date else None,
    }
    if flagged:
        result["warning"] = f"Currency conversion failed: {flag_reason}. ILS amount is not set."
    return result


async def exec_request_confirmation(
    group_id: str, is_admin: bool,
    action: str, params: dict, description: str,
    sender: str = "",
    resolved_phone: str = "",
    **_,
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    # Validate required params for each action before asking for confirmation
    if action == "remove_invoice":
        if not params.get("invoice_id"):
            return {"error": "invoice_id is required for remove_invoice action."}
    elif action == "send_email":
        if not params.get("to"):
            return {"error": "'to' is required for send_email action."}
    elif action == "set_invoice_amount":
        if not params.get("invoice_id"):
            return {"error": "invoice_id is required for set_invoice_amount action."}
        if params.get("new_amount") is None:
            return {"error": "new_amount is required for set_invoice_amount action."}
    elif action == "add_date_format":
        if not params.get("format_string"):
            return {"error": "format_string is required for add_date_format action."}

    # For send_email, validate the recipient NOW (before the user confirms) so
    # we don't ask for confirmation and then fail. Also surface the exact address
    # in the confirmation text so the user sees exactly where the report is going.
    if action == "send_email":
        from app.tools.send_email_tool import _is_allowed

        to_email = params.get("to", "").strip()
        if not to_email:
            return {"error": "No email address provided."}
        if not _is_allowed(to_email):
            return {
                "error": (
                    f"Email address '{to_email}' is not in the allowed recipient list. "
                    f"Ask an admin to add it via the Settings panel."
                )
            }
        # Append recipient to description so the user sees it in the confirmation prompt
        description = f"{description} — will be sent to {to_email}"

    # Prefer the resolved canonical phone over the raw sender JID/LID — using
    # the raw value here caused the confirmation intercept below (which
    # compares against the resolved sender_phone) to permanently reject the
    # original requester's own "yes" whenever WhatsApp sent a LID.
    from app.utils.phone import resolve_sender_phone
    staged_by = resolve_sender_phone({"resolved_phone": resolved_phone, "sender": sender})
    from app.agent.confirmation import confirmation_store
    if not confirmation_store.set(group_id, action, params, description, staged_by=staged_by):
        return {
            "error": "⚠️ Another action is already pending for this group. Please reply 'yes' to confirm or 'no' to cancel it before requesting a new action."
        }
    return {"pending": True, "description": description, "ttl_minutes": 5}


# ── Confirmed action executors (not exposed as tools) ────────────────────────

async def exec_remove_invoice(group_id: str, invoice_id: str) -> dict:
    # Guard against passing a display number (e.g. "3") instead of a UUID
    if invoice_id.isdigit():
        return {"error": f"'{invoice_id}' is a display number, not an invoice ID. Call list_invoices first to get the correct UUID."}
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": f"Invoice {invoice_id} not found."}
        db.delete(invoice)
        db.commit()
    return {"ok": True, "deleted": invoice_id}


async def exec_send_email(group_id: str, params: dict) -> dict:
    from app.reports.generator import generate_and_email_report
    from app.tools.send_email_tool import _is_allowed

    cur_month, cur_year = _current_month_year()
    to_email = params.get("to_email", "").strip()

    if not to_email:
        return {"error": "No email address provided."}

    if not _is_allowed(to_email):
        logger.warning("Blocked email send attempt to non-allowlisted address: %s", to_email)
        return {
            "error": (
                f"Email address '{to_email}' is not in the allowed recipient list. "
                f"Ask an admin to add it via the Settings panel."
            )
        }

    return await generate_and_email_report(
        group_id=group_id,
        month=params.get("month") or cur_month,
        year=params.get("year") or cur_year,
        to_email=to_email,
        fmt=params.get("format", "pdf"),
        attach_images=bool(params.get("attach_images", False)),
        force_dual_currency=params.get("dual_currency"),
        start_date=params.get("start_date"),
        end_date=params.get("end_date"),
    )


# ── Dispatcher ────────────────────────────────────────────────────────────────

EXECUTORS = {
    "get_status":           exec_get_status,
    "list_invoices":        exec_list_invoices,
    "get_invoice_summary":  exec_get_preview,
    "update_config":        exec_update_config,
    "generate_report":      exec_generate_report,
    "flag_invoice":         exec_flag_invoice,
    "unflag_invoice":       exec_unflag_invoice,
    "set_invoice_date":     exec_set_invoice_date,
    "set_invoice_amount":   exec_set_invoice_amount,
    "add_date_format":      exec_add_date_format,
    "stage_action": exec_request_confirmation,
}


async def execute_tool(name: str, inputs: dict, group_id: str, is_admin: bool) -> dict:
    executor = EXECUTORS.get(name)
    if not executor:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await executor(group_id=group_id, is_admin=is_admin, **inputs)
    except Exception as exc:
        logger.exception("Tool %s raised: %s", name, exc)
        return {"error": f"Tool execution failed: {exc}"}
