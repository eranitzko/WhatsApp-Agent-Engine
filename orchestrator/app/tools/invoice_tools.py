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

# ── Tool schemas (imported from agent/tools.py, cache_control stripped) ──
# cache_control is stripped here because the ToolRegistry / AgentRunner is
# responsible for applying prompt-cache hints at the Claude call site.

_SCHEMA_BY_NAME: dict[str, dict] = {
    s["name"]: ({k: v for k, v in s.items() if k != "cache_control"} | {"category": "invoices"})
    for s in _orig.TOOL_SCHEMAS
}


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
        confirmation_store  — ConfirmationStore instance (used by stage_action)
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


# ── stage_action — inline implementation ─────────────────────────────────────
# Re-implemented inline rather than delegating to the original
# exec_request_confirmation, which imports confirmation_store at module load
# time and therefore cannot be patched at call time.  This also eliminates the
# race condition that existed in the previous adapter.

async def _exec_request_confirmation(params: dict, **ctx) -> str:
    confirmation_store = ctx.get("confirmation_store")
    if not confirmation_store:
        return "Error: confirmation store not available."
    group_jid = ctx.get("group_jid", "")
    action = params.get("action", "")
    action_params = params.get("params", {})
    description = params.get("description", action)

    sender_raw = ctx.get("sender", "")
    staged_by = sender_raw.split("@")[0].split(":")[0] if sender_raw else ""

    try:
        if not confirmation_store.set(group_jid, action, action_params, description, staged_by=staged_by):
            return "⚠️ Another action is already pending for this group. Please reply 'yes' to confirm or 'no' to cancel it before requesting a new action."
        return f"Confirmation requested: {description}. Reply 'yes' to confirm or 'no' to cancel."
    except Exception as exc:
        logger.exception("invoice tool request_confirmation raised: %s", exc)
        return f"Error requesting confirmation: {str(exc)}"


# ── Public factory ────────────────────────────────────────────────────────────

def get_invoice_tools(db_session_factory=None, **kwargs) -> dict[str, dict]:
    """Return all 12 invoice tools in ToolRegistry format.

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
    # Build wrapped executors for the 11 straightforward tools
    _tool_executor_pairs = [
        ("get_status",           _orig.exec_get_status),
        ("list_invoices",        _orig.exec_list_invoices),
        ("get_invoice_summary",  _orig.exec_get_preview),
        ("update_config",        _orig.exec_update_config),
        ("save_invoice",         _orig.exec_save_invoice),
        ("flag_invoice",         _orig.exec_flag_invoice),
        ("unflag_invoice",       _orig.exec_unflag_invoice),
        ("set_invoice_date",     _orig.exec_set_invoice_date),
        ("set_invoice_amount",   _orig.exec_set_invoice_amount),
        ("add_date_format",      _orig.exec_add_date_format),
    ]

    registry: dict[str, dict] = {}

    for name, orig_fn in _tool_executor_pairs:
        registry[name] = {
            "schema":   _SCHEMA_BY_NAME[name],
            "executor": _make_executor(orig_fn, name),
        }

    # stage_action uses inline implementation (avoids import-time singleton issue)
    registry["stage_action"] = {
        "schema":   _SCHEMA_BY_NAME["stage_action"],
        "executor": _exec_request_confirmation,
    }

    return registry
