"""Tool definitions (Claude format) and their async executors.

Tools exposed to Claude:
  get_status, list_invoices, get_preview, update_config,
  generate_report, flag_invoice, unflag_invoice, set_invoice_date, request_confirmation

remove_invoice and send_report_by_email are NOT exposed as tools.
They execute only via the confirmation callback in agent.py.

Admin enforcement happens here in code, independent of Claude's reasoning.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
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
            "Get the current bot status, group configuration, and invoice statistics. "
            "Available to all group members."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_invoices",
        "description": (
            "List invoices recorded for a given month, with their IDs, dates, vendors, "
            "amounts, and flag status. Defaults to the current month. Available to all members."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "Month number 1–12. Defaults to current month."},
                "year":  {"type": "integer", "description": "4-digit year. Defaults to current year."},
            },
            "required": [],
        },
    },
    {
        "name": "get_preview",
        "description": (
            "Get a summary for a given month: invoice count, total in ILS, "
            "number of flagged invoices. Defaults to current month. Available to all members."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "Month number 1–12. Defaults to current month."},
                "year":  {"type": "integer", "description": "4-digit year. Defaults to current year."},
            },
            "required": [],
        },
    },
    {
        "name": "update_config",
        "description": (
            "Update a group configuration setting. Admin only. "
            "Keys: 'header' (report title), 'author' (report author name), "
            "'language' (en or he), 'dual-currency' (on or off)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key":   {"type": "string", "enum": ["header", "author", "language", "dual-currency"]},
                "value": {"type": "string", "description": "New value for the setting."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "flag_invoice",
        "description": "Flag an invoice for manual review. Admin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to flag."},
                "reason":     {"type": "string", "description": "Reason for flagging. Optional."},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "unflag_invoice",
        "description": "Remove the review flag from an invoice. Admin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to unflag."},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "set_invoice_date",
        "description": (
            "Correct the date on an invoice. Admin only. Use when OCR extracted the wrong date "
            "(e.g. day/month swapped). Also re-calculates the ILS amount if the currency is not ILS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to update."},
                "new_date":   {"type": "string", "description": "Correct date in YYYY-MM-DD format."},
            },
            "required": ["invoice_id", "new_date"],
        },
    },
    {
        "name": "set_invoice_amount",
        "description": (
            "Correct the original amount on an invoice. Admin only. "
            "Re-calculates the ILS amount using the existing exchange rate for the invoice date. "
            "Always call request_confirmation first — never call this directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to update."},
                "new_amount":  {
                    "type": "number",
                    "description": "Correct amount in the invoice's original currency. Must be positive.",
                },
            },
            "required": ["invoice_id", "new_amount"],
        },
    },
    {
        "name": "add_date_format",
        "description": (
            "Register an extra date format used globally when parsing invoice dates. "
            "Adds to existing formats — does not replace them. Admin only. "
            "Always call request_confirmation first — never call this directly."
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
    },
    {
        "name": "request_confirmation",
        "description": (
            "Request admin confirmation before executing a destructive or external action. "
            "Use this before removing an invoice or sending a report by email. "
            "After calling this tool, tell the user what will happen and ask them to reply 'yes' to confirm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action":      {"type": "string", "enum": ["remove_invoice", "send_email", "set_invoice_amount", "add_date_format"], "description": "Action to confirm."},
                "params": {
                    "type": "object",
                    "description": (
                        "Parameters for the action. "
                        "For remove_invoice: {invoice_id}. "
                        "For send_email: {to_email, month (optional), year (optional), "
                        "start_date (optional YYYY-MM-DD), end_date (optional YYYY-MM-DD), "
                        "format ('pdf'|'excel'|'both'), attach_images (bool), dual_currency (bool|null)}. "
                        "For set_invoice_amount: {invoice_id, new_amount}. "
                        "For add_date_format: {format_string}. "
                    ),
                },
                "description": {"type": "string", "description": "Short label identifying what will be removed or sent (e.g. vendor, date, amount). No warnings or caveats."},
            },
            "required": ["action", "params", "description"],
        },
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

    from decimal import Decimal
    try:
        amount = Decimal(str(new_amount))
    except Exception:
        return {"error": f"Invalid amount '{new_amount}'."}
    if amount <= 0:
        return {"error": "Amount must be positive."}

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


async def exec_request_confirmation(
    group_id: str, is_admin: bool,
    action: str, params: dict, description: str,
    **_,
) -> dict:
    if not is_admin:
        return {"error": "Admin only."}

    # Validate required params for each action before asking for confirmation
    if action == "remove_invoice":
        if not params.get("invoice_id"):
            return {"error": "invoice_id is required for remove_invoice action."}
    elif action == "send_email":
        if not params.get("to_email"):
            return {"error": "to_email is required for send_email action."}
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
        from app.config import settings
        allowlist_raw = settings.report_email_allowlist.strip()
        if allowlist_raw:
            allowlist = {addr.strip().lower() for addr in allowlist_raw.split(",") if addr.strip()}
        else:
            allowlist = {settings.gmail_user.strip().lower()} if settings.gmail_user else set()

        to_email = params.get("to_email", "").strip()
        if not to_email:
            return {"error": "No email address provided."}
        if allowlist and to_email.lower() not in allowlist:
            return {
                "error": (
                    f"Email address '{to_email}' is not in the allowed recipient list. "
                    f"Configure REPORT_EMAIL_ALLOWLIST to permit additional addresses."
                )
            }
        # Append recipient to description so the user sees it in the confirmation prompt
        description = f"{description} — will be sent to {to_email}"

    from app.agent.confirmation import confirmation_store
    confirmation_store.set(group_id, action, params, description)
    return {"pending": True, "description": description, "ttl_minutes": 5}


# ── Confirmed action executors (not exposed as tools) ────────────────────────

async def exec_remove_invoice(group_id: str, invoice_id: str) -> dict:
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if not invoice or invoice.group_id != group_id:
            return {"error": f"Invoice {invoice_id} not found."}
        db.delete(invoice)
        db.commit()
    return {"ok": True, "deleted": invoice_id}


async def exec_send_email(group_id: str, params: dict) -> dict:
    from app.reports.generator import generate_and_email_report
    from app.config import settings

    # Build the effective allowlist: explicit env var or fall back to the gmail sender
    allowlist_raw = settings.report_email_allowlist.strip()
    if allowlist_raw:
        allowlist = {addr.strip().lower() for addr in allowlist_raw.split(",") if addr.strip()}
    else:
        # No allowlist configured — only allow sending to self
        allowlist = {settings.gmail_user.strip().lower()} if settings.gmail_user else set()

    cur_month, cur_year = _current_month_year()
    to_email = params.get("to_email", "").strip()

    if not to_email:
        return {"error": "No email address provided."}

    if allowlist and to_email.lower() not in allowlist:
        logger.warning("Blocked email send attempt to non-allowlisted address: %s", to_email)
        return {
            "error": (
                f"Email address '{to_email}' is not in the allowed recipient list. "
                f"Configure REPORT_EMAIL_ALLOWLIST to permit additional addresses."
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
    "get_preview":          exec_get_preview,
    "update_config":        exec_update_config,
    "generate_report":      exec_generate_report,
    "flag_invoice":         exec_flag_invoice,
    "unflag_invoice":       exec_unflag_invoice,
    "set_invoice_date":     exec_set_invoice_date,
    "set_invoice_amount":   exec_set_invoice_amount,
    "add_date_format":      exec_add_date_format,
    "request_confirmation": exec_request_confirmation,
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
