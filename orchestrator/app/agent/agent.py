"""InvoiceAgent — Claude-powered agent for the Invoice Curator WhatsApp bot.

Responsibilities:
  - Handles text events (natural language + slash commands)
  - Handles image events (proactive commentary after pipeline processes the image)
  - Runs the Claude tool-use loop with prompt caching
  - Manages per-group conversation context
  - Intercepts pending confirmation replies before passing to Claude
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import anthropic

from app.agent.confirmation import confirmation_store
from app.agent.context import context_store
from app.agent.tools import (
    TOOL_SCHEMAS, execute_tool,
    exec_remove_invoice, exec_send_email,
    exec_set_invoice_amount, exec_add_date_format,
)
from app.bridge_client import send_text
from app.config import settings
from app.db.models import GroupConfig
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 6   # safety limit on agentic loops

# ── System prompt ─────────────────────────────────────────────────────────────

_STATIC_SYSTEM = """\
You are Invoice Curator, an AI assistant embedded in a WhatsApp group to help manage business invoices and generate billing reports.

## What you do
- When group members send invoice photos, the system extracts structured data automatically. You receive the result as text and decide whether to save it, flag it, or ask for clarification.
- You help admins generate monthly reports, manage invoice records, and configure the system.
- You respond to both natural language and slash commands (/report, /status, /set, etc.).
- You respond concisely — this is WhatsApp, not a web app.

## Language
- Always respond in the language specified in the group configuration (en or he).
- For Hebrew, write right-to-left friendly text. Do not mix languages within a response.

## Proactive behaviour
- If an extracted invoice looks like a duplicate (same vendor + amount + approximate date), say so clearly.
- If an amount seems unusually large compared to the group's typical invoices, flag it.
- At the end of a month, remind admins that a report can be generated.

## Tool rules
- update_config, generate_report, flag_invoice, unflag_invoice, set_invoice_date, request_confirmation → admin only. If the user is not an admin, decline politely and do not call the tool.
- For remove_invoice, set_invoice_amount, add_date_format, or sending email outside the group: ALWAYS call request_confirmation first. Never execute these directly.
- After calling request_confirmation, tell the user exactly what will happen and ask them to reply "yes" (or "כן") to confirm.
- If a deletion was already confirmed and executed, do NOT offer to delete the same invoice again. The invoice is gone.
- When an invoice date looks wrong, prefer set_invoice_date over asking the user to delete and re-upload.
- If a tool returns {"error": ...}, relay the error to the user clearly.
- If a tool returns {"status": "coming_soon"}, tell the user this feature is coming soon.

## Referencing invoices
- Users will refer to invoices by vendor name, date, amount, or printed invoice number — never by internal ID.
- When you need an internal ID to call a tool, call list_invoices first to find the matching record, then use its id silently.
- Never show internal UUIDs to users. Never ask a user to provide an ID.
- If multiple invoices match a description, list them briefly (vendor, date, amount) and ask the user to clarify.

## Scope
- You only handle invoice management: receiving invoices, querying records, generating reports, and configuring the bot.
- If a message is unrelated to invoices or bot configuration, decline politely in one sentence and do not engage further. Do not answer general questions, give advice, chat, or respond to anything outside this scope.

## Response style
- Be direct and brief. One or two sentences is usually enough.
- Use plain text — no markdown headers, no asterisks for bold (WhatsApp renders these poorly).
- For lists, use simple dashes or numbers.\
"""

# ── Agent class ───────────────────────────────────────────────────────────────

class InvoiceAgent:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _build_system(self, config: GroupConfig, is_admin: bool) -> list[dict]:
        """Two-block system: static (cached) + dynamic (fresh each turn)."""
        dynamic = (
            f"\n## Current group configuration\n"
            f"- Language: {config.feedback_language}\n"
            f"- Lead currency: {config.lead_currency}\n"
            f"- Report header: {config.report_header or '(not set)'}\n"
            f"- Report author: {config.report_author or '(not set)'}\n"
            f"- Dual currency columns: {'forced on' if config.force_dual_currency else 'auto'}\n"
            f"\n## Current request context\n"
            f"- Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"- User is group admin: {is_admin}\n"
        )
        return [
            {"type": "text", "text": _STATIC_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic},
        ]

    def _get_config(self, group_id: str) -> GroupConfig:
        with SessionLocal() as db:
            config = db.get(GroupConfig, group_id)
            if not config:
                config = GroupConfig(group_id=group_id)
                db.add(config)
                db.commit()
                db.refresh(config)
            # Detach from session by reading all attributes
            db.expunge(config)
            return config

    async def handle_text_event(self, event: dict) -> None:
        jid: str       = event["jid"]
        sender: str    = event["sender"]
        is_admin: bool = event.get("isAdmin", False)
        text: str      = event.get("text", "").strip()

        if not text:
            return

        config = self._get_config(jid)

        # ── Confirmation intercept ─────────────────────────────────────────
        pending = confirmation_store.get(jid)
        if pending:
            ctx = context_store.get(jid)
            if confirmation_store.is_confirm(text):
                confirmation_store.clear(jid)
                result = await self._execute_confirmed(jid, pending)
                # Record the exchange in context so Claude knows the action completed
                ctx.add("user", f"[{sender}]: {text}")
                ctx.add("assistant", result)
                await send_text(jid, result)
            else:
                confirmation_store.clear(jid)
                if confirmation_store.is_cancel(text):
                    # Explicit cancel word — notify the user and stop
                    msg = "Cancelled." if config.feedback_language == "en" else "בוטל."
                    ctx.add("user", f"[{sender}]: {text}")
                    ctx.add("assistant", msg)
                    await send_text(jid, msg)
                else:
                    # Unrelated message — cancel silently and process as a new request
                    await self._run(jid, sender, is_admin, text, config)
            return

        await self._run(jid, sender, is_admin, text, config)

    async def handle_image_event(self, event: dict) -> None:
        """Called after the invoice pipeline has processed an image.

        `event` must contain an 'extraction' key with the pipeline result dict.
        """
        jid: str       = event["jid"]
        sender: str    = event["sender"]
        is_admin: bool = event.get("isAdmin", False)
        extraction     = event.get("extraction", {})

        if extraction.get("error"):
            msg = (
                f"[System] Invoice from {sender} could not be processed: {extraction['error']}\n"
                f"Inform the user briefly. Do not suggest any action unless the error is recoverable."
            )
        elif extraction.get("duplicate"):
            existing_amt = extraction.get("existing_amount") or extraction.get("extracted_amount", "")
            existing_vnd = extraction.get("existing_vendor") or extraction.get("extracted_vendor", "")
            existing_dt  = extraction.get("existing_date") or extraction.get("extracted_date", "")
            msg = (
                f"[System] This invoice is already in the system — NOT saved again.\n"
                f"Existing record: vendor={existing_vnd}, date={existing_dt}, amount={existing_amt}.\n"
                f"Tell the user in one short line that this invoice is already saved, "
                f"including vendor, date, and amount. Nothing else — no offers, no suggestions."
            )
        else:
            msg = (
                f"[System] New invoice received from {sender} and saved to the database.\n"
                f"Extracted data: {extraction}\n"
                f"Acknowledge briefly. Flag any concerns about the amounts if warranted."
            )

        config = self._get_config(jid)
        await self._run(jid, sender, is_admin, msg, config, is_system=True)

    async def _run(
        self,
        jid: str,
        sender: str,
        is_admin: bool,
        text: str,
        config: GroupConfig,
        is_system: bool = False,
    ) -> None:
        ctx = context_store.get(jid)
        system = self._build_system(config, is_admin)

        # Add user message to history
        user_content = text if is_system else f"[{sender}]: {text}"
        ctx.add("user", user_content)

        messages = list(ctx.messages)  # snapshot

        try:
            response_text = await self._tool_loop(jid, is_admin, system, messages)
        except anthropic.APIError as exc:
            logger.error("Claude API error for group %s: %s", jid, exc)
            response_text = (
                "Sorry, I'm having trouble connecting to my AI backend. Please try again in a moment."
                if config.feedback_language == "en"
                else "מצטער, יש לי בעיה בתקשורת עם השרת. נסה שוב בעוד רגע."
            )

        ctx.add("assistant", response_text)
        await send_text(jid, response_text)

    async def _tool_loop(
        self,
        jid: str,
        is_admin: bool,
        system: list[dict],
        messages: list[dict],
    ) -> str:
        """Run Claude with tool use until a final text response is produced."""
        for _ in range(MAX_TOOL_TURNS):
            response = await self._client.messages.create(
                model=settings.claude_model,
                max_tokens=1024,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                # Extract text from response
                return next(
                    (b.text for b in response.content if hasattr(b, "text")),
                    ""
                )

            if response.stop_reason != "tool_use":
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                return next((b.text for b in response.content if hasattr(b, "text")), "")

            # Collect tool calls
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            # Append assistant turn with tool calls
            messages.append({"role": "assistant", "content": response.content})

            # Execute tools and collect results
            tool_results = []
            for tu in tool_uses:
                result = await execute_tool(tu.name, dict(tu.input), jid, is_admin)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result),
                })

            messages.append({"role": "user", "content": tool_results})

        # Turn limit exhausted — the intermediate tool-use turns exist only in the
        # local messages snapshot and are not in ctx.messages, so no stripping is needed.
        # Log a warning so the issue is visible in production.
        logger.warning(
            "Tool loop exhausted for group %s after %d turns — returning fallback response",
            jid,
            MAX_TOOL_TURNS,
        )
        return "I wasn't able to complete that action. Please try again."

    async def _execute_confirmed(self, jid: str, pending) -> str:
        """Execute a confirmed destructive action."""
        if pending.action == "remove_invoice":
            result = await exec_remove_invoice(jid, pending.params.get("invoice_id", ""))
        elif pending.action == "send_email":
            result = await exec_send_email(jid, pending.params)
        elif pending.action == "set_invoice_amount":
            result = await exec_set_invoice_amount(
                group_id=jid,
                is_admin=True,
                invoice_id=pending.params.get("invoice_id", ""),
                new_amount=pending.params.get("new_amount", 0),
            )
        elif pending.action == "add_date_format":
            result = await exec_add_date_format(
                group_id=jid,
                is_admin=True,
                format_string=pending.params.get("format_string", ""),
            )
        else:
            return f"Unknown confirmed action: {pending.action}"

        if result.get("ok"):
            return f"Done: {pending.description}"
        return f"Failed: {result.get('error', 'unknown error')}"


# Singleton
agent = InvoiceAgent()
