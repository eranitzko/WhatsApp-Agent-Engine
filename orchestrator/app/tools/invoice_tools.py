"""Invoice Curator tools in ToolRegistry format.

Exposes all 11 invoice tools as:
    {tool_name: {"schema": <Claude tool schema dict>, "executor": async fn}}

Each executor has signature:
    async def executor(params: dict, **ctx) -> str

Where ctx contains: group_jid, sender, is_admin, confirmation_store (injected
by AgentRunner).  The underlying DB access uses SessionLocal directly — each
executor opens and closes its own session, matching the pattern in
app/agent/tools.py.

Usage:
    from app.tools.invoice_tools import get_invoice_tools

    tools = get_invoice_tools()
    result = await tools["get_status"]["executor"]({}, group_jid="...", is_admin=False)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent import tools as _orig

logger = logging.getLogger(__name__)

# ── Tool schemas (copied verbatim from agent/tools.py, without cache_control) ──
# cache_control is stripped here because the ToolRegistry / AgentRunner is
# responsible for applying prompt-cache hints at the Claude call site.

_RAW_SCHEMAS: list[dict] = [
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
        "name": "generate_report",
        "description": (
            "Generate a monthly invoice report and send it to the group. Admin only. "
            "For email delivery, use request_confirmation first, then send_report_by_email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month":         {"type": "integer", "description": "Month 1–12. Defaults to current month. Ignored if start_date/end_date provided."},
                "year":          {"type": "integer", "description": "4-digit year. Defaults to current year. Ignored if start_date/end_date provided."},
                "start_date":    {"type": "string", "description": "Range start date YYYY-MM-DD (optional). Use instead of month/year for custom periods."},
                "end_date":      {"type": "string", "description": "Range end date YYYY-MM-DD (optional, inclusive). Required when start_date is set."},
                "format":        {"type": "string", "enum": ["pdf", "excel", "both"], "description": "Report format. Default: pdf."},
                "attach_images": {"type": "boolean", "description": "Embed invoice images in PDF appendix. Default: false."},
                "dual_currency": {"type": "boolean", "description": "Force dual currency columns. Null = auto-detect."},
            },
            "required": [],
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

# Index schemas by name for quick lookup
_SCHEMA_BY_NAME: dict[str, dict] = {s["name"]: s for s in _RAW_SCHEMAS}


# ── Adapter helpers ───────────────────────────────────────────────────────────

def _result_to_str(result: Any) -> str:
    """Serialise a tool result to a string for the AgentRunner."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def _make_executor(orig_fn, tool_name: str):
    """
    Wrap an original executor from agent/tools.py into the ToolRegistry signature.

    Original signature:  async def exec_*(group_id, is_admin, **kwargs) -> dict
    New signature:       async def executor(params: dict, **ctx) -> str

    ctx keys provided by AgentRunner:
        group_jid           — the WhatsApp group JID
        sender              — sender JID (not used by most tools)
        is_admin            — bool
        confirmation_store  — ConfirmationStore instance (used by request_confirmation)
    """
    async def executor(params: dict, **ctx) -> str:
        group_jid = ctx.get("group_jid", "")
        is_admin = ctx.get("is_admin", False)
        try:
            result = await orig_fn(
                group_id=group_jid,
                is_admin=is_admin,
                **params,
            )
        except Exception as exc:
            logger.exception("invoice tool %s raised: %s", tool_name, exc)
            return json.dumps({"error": f"Tool execution failed: {exc}"})
        return _result_to_str(result)

    executor.__name__ = f"invoice_{tool_name}"
    return executor


# ── request_confirmation needs special handling ───────────────────────────────
# The original exec_request_confirmation imports and uses the module-level
# singleton `confirmation_store` from app.agent.confirmation.  For the new
# ToolRegistry world the AgentRunner may inject a different ConfirmationStore
# via ctx["confirmation_store"].  We honour that if provided, otherwise fall
# back to the singleton so the legacy code path is unaffected.

async def _exec_request_confirmation_adapted(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)

    # If the AgentRunner injects a store, patch it into the module so the
    # original function uses it; otherwise the original singleton is used.
    injected_store = ctx.get("confirmation_store")

    if injected_store is not None:
        import app.agent.confirmation as _conf_mod
        _original_store = _conf_mod.confirmation_store
        _conf_mod.confirmation_store = injected_store
        try:
            result = await _orig.exec_request_confirmation(
                group_id=group_jid,
                is_admin=is_admin,
                **params,
            )
        except Exception as exc:
            logger.exception("invoice tool request_confirmation raised: %s", exc)
            return json.dumps({"error": f"Tool execution failed: {exc}"})
        finally:
            _conf_mod.confirmation_store = _original_store
    else:
        try:
            result = await _orig.exec_request_confirmation(
                group_id=group_jid,
                is_admin=is_admin,
                **params,
            )
        except Exception as exc:
            logger.exception("invoice tool request_confirmation raised: %s", exc)
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    return _result_to_str(result)


# ── Public factory ────────────────────────────────────────────────────────────

def get_invoice_tools() -> dict[str, dict]:
    """Return all 11 invoice tools in ToolRegistry format.

    Returns:
        {
            tool_name: {
                "schema":   <Claude tool schema dict>,
                "executor": async fn(params: dict, **ctx) -> str,
            },
            ...
        }

    The db_session_factory argument is accepted for API compatibility but is
    not used: the underlying executors open their own sessions via SessionLocal
    (the same pattern used in app/agent/tools.py).
    """
    # Build wrapped executors for the 10 straightforward tools
    _tool_executor_pairs = [
        ("get_status",         _orig.exec_get_status),
        ("list_invoices",      _orig.exec_list_invoices),
        ("get_preview",        _orig.exec_get_preview),
        ("update_config",      _orig.exec_update_config),
        ("generate_report",    _orig.exec_generate_report),
        ("flag_invoice",       _orig.exec_flag_invoice),
        ("unflag_invoice",     _orig.exec_unflag_invoice),
        ("set_invoice_date",   _orig.exec_set_invoice_date),
        ("set_invoice_amount", _orig.exec_set_invoice_amount),
        ("add_date_format",    _orig.exec_add_date_format),
    ]

    registry: dict[str, dict] = {}

    for name, orig_fn in _tool_executor_pairs:
        registry[name] = {
            "schema":   _SCHEMA_BY_NAME[name],
            "executor": _make_executor(orig_fn, name),
        }

    # request_confirmation gets its own adapter (confirmation_store injection)
    registry["request_confirmation"] = {
        "schema":   _SCHEMA_BY_NAME["request_confirmation"],
        "executor": _exec_request_confirmation_adapted,
    }

    return registry
