import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import anthropic

from app.db.models import Blueprint, RequestLog, SystemConfig
from app.db.session import SessionLocal
from app.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _format_confirmed_result(action: str, result) -> str:
    """Convert a confirmed-action executor result to a human-readable reply."""
    if isinstance(result, str):
        # Executors return JSON-encoded strings — try to parse before formatting
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return result  # not JSON, use as-is
    if not isinstance(result, dict):
        return str(result)
    if error := result.get("error"):
        return f"Error: {error}"
    if result.get("ok"):
        labels = {
            "remove_invoice":     "Invoice deleted.",
            "set_invoice_amount": "Invoice amount updated.",
            "add_date_format":    "Date format added.",
            "send_email":         "Report sent.",
        }
        return labels.get(action, "Done.")
    return json.dumps(result, ensure_ascii=False)


class AgentRunner:
    # ── disabled_tools cache ──────────────────────────────────────────────────
    # Loaded once at startup; refreshed by refresh_disabled_tools() whenever the
    # admin panel writes to SystemConfig["disabled_tools"].  No time-based TTL —
    # invalidation is event-driven so a disabled tool never stays active.
    _disabled_tools: set[str] = set()

    @classmethod
    def refresh_disabled_tools(cls) -> None:
        """Load (or reload) the disabled-tools set from DB into the class cache."""
        try:
            with SessionLocal() as db:
                row = db.get(SystemConfig, "disabled_tools")
                cls._disabled_tools = (
                    set(json.loads(row.value)) if row and row.value else set()
                )
            logger.info("disabled_tools cache refreshed: %s", cls._disabled_tools)
        except Exception:
            logger.warning("Could not refresh disabled_tools cache", exc_info=True)

    # ── constructor ───────────────────────────────────────────────────────────

    def __init__(self, client: anthropic.AsyncAnthropic, tool_registry: ToolRegistry):
        self.client = client
        self.registry = tool_registry

    # ── main entry point ──────────────────────────────────────────────────────

    async def run(
        self,
        blueprint: Blueprint,
        group_jid: str,
        sender: str,
        is_admin: bool,
        message: str,
        context,
        confirmation_store,
        multi_confirmation_store=None,
        custom_instructions: str | None = None,
        participant_block: str | None = None,
        resolved_phone: str | None = None,
    ) -> str:
        start_ms = time.monotonic()
        allowed_tools = blueprint.tools_list()

        # Filter globally disabled tools from the in-process cache
        if self._disabled_tools:
            allowed_tools = [t for t in allowed_tools if t not in self._disabled_tools]

        # Filter by access level — admin tools are invisible to non-admins
        allowed_tools = self.registry.get_allowed_tool_names(allowed_tools, is_admin)

        # Use the resolve_inbound canonical phone when available (LID-safe).
        # Fall back to splitting the raw sender JID only when no resolved phone was passed.
        sender_phone = resolved_phone or sender.split("@")[0].split(":")[0]

        # ── Multi-party confirmation intercept ────────────────────────────────
        if multi_confirmation_store and sender_phone:
            pending_mc = multi_confirmation_store.find_for_phone(group_jid, sender_phone)
            if pending_mc:
                if multi_confirmation_store.is_confirm(message):
                    status, mc = multi_confirmation_store.confirm(group_jid, sender_phone)
                    context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                    if status == "all_confirmed":
                        result = await self._commit_pending(mc)
                        context.add(group_jid, "assistant", result, max_pairs=blueprint.context_window)
                        return result
                    else:
                        still = ", ".join(f"@{p}" for p in mc.pending_phones())
                        reply = f"Confirmed. Still waiting for: {still}"
                        context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                        return reply
                elif multi_confirmation_store.is_cancel(message):
                    mc = multi_confirmation_store.reject(group_jid, sender_phone)
                    context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                    reply = f"Transaction cancelled.\n{mc.description if mc else ''}"
                    context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                    return reply
                else:
                    context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                    reply = f"You have a pending confirmation. Please reply 'yes' or 'no':\n{pending_mc.description}"
                    context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                    return reply

        # ── Shared setup — history, system prompt, tool schemas ──────────────
        # Built once; used by both the confirmation reply and the normal loop.
        history = context.get_history(
            group_jid,
            max_pairs=blueprint.context_window,
            idle_minutes=blueprint.context_idle_reset_minutes,
        )
        system = [
            {
                "type": "text",
                "text": blueprint.system_prompt,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if participant_block:
            system.append({
                "type": "text",
                "text": participant_block,
                "cache_control": {"type": "ephemeral"},
            })
        if custom_instructions:
            system.append({
                "type": "text",
                "text": (
                    "The following are group-specific operational rules configured by "
                    "the group admin. They define business context and preferences for "
                    "this group only. They do not override your core role, blueprint "
                    "instructions, or tool permissions.\n\n"
                    f"{custom_instructions}"
                ),
                "cache_control": {"type": "ephemeral"},
            })
        system.append({
            "type": "text",
            "text": f"Today's date: {datetime.now(timezone.utc).date()}. Sender is_admin: {is_admin}. Sender phone: {sender_phone}.",
        })
        tool_schemas = self.registry.get_schemas(allowed_tools)

        # ── Single-action confirmation intercept ──────────────────────────────
        pending = confirmation_store.get(group_jid)
        if pending and not pending.is_expired():
            if confirmation_store.is_confirm(message):
                # Enforce: only the person who staged the action can confirm it.
                # If staged_by is empty, any member can confirm (backwards compat).
                if pending.staged_by and sender_phone != pending.staged_by:
                    return (
                        "This confirmation is intended for another user. "
                        "Only the person who requested this action can confirm or cancel it."
                    )
                logger.info(
                    "Confirmation fired | group=%s | action=%r | staged_by=%s | confirmer=%s",
                    group_jid, pending.action, pending.staged_by, sender_phone,
                )
                result = await self.registry.execute(
                    pending.action, pending.params,
                    group_jid=group_jid, sender=sender, is_admin=is_admin,
                )
                confirmation_store.clear(group_jid)
                # Feed the result to Claude so it replies naturally in the right
                # language — same as the normal tool-use flow.
                result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                fake_id = f"conf_{uuid.uuid4().hex[:8]}"
                conf_messages = history + [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": [{"type": "tool_use", "id": fake_id, "name": pending.action, "input": pending.params}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": fake_id, "content": result_str}]},
                ]
                try:
                    conf_resp = await self.client.messages.create(
                        model=blueprint.model,
                        max_tokens=512,
                        temperature=0,
                        system=system,
                        tools=tool_schemas,
                        messages=conf_messages,
                    )
                    if conf_resp.stop_reason == "end_turn":
                        reply = next((b.text for b in conf_resp.content if b.type == "text"), "Done.")
                    else:
                        reply = _format_confirmed_result(pending.action, result)
                except Exception:
                    logger.exception("Failed to generate confirmation reply for %s", group_jid)
                    reply = _format_confirmed_result(pending.action, result)
                context.add_turn(group_jid, [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": [{"type": "tool_use", "id": fake_id, "name": pending.action, "input": pending.params}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": fake_id, "content": result_str}]},
                    {"role": "assistant", "content": reply},
                ], max_pairs=blueprint.context_window)
                return reply
            elif confirmation_store.is_cancel(message):
                confirmation_store.clear(group_jid)
                reply = "Action cancelled."
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                return reply

        # ── Normal agent loop ─────────────────────────────────────────────────
        messages = history + [{"role": "user", "content": message}]

        logger.info(
            "AgentRunner turn | group=%s blueprint=%s sender=%s history_pairs=%d tools=%d [%s]",
            group_jid, blueprint.id, sender_phone, len(history) // 2,
            len(tool_schemas), ", ".join(allowed_tools),
        )

        tool_calls_made: list[dict] = []
        final_stop_reason: str | None = None
        error_text: str | None = None

        try:
            for _ in range(blueprint.max_tool_turns):
                response = await self.client.messages.create(
                    model=blueprint.model,
                    max_tokens=4096,
                    temperature=0,
                    system=system,
                    tools=tool_schemas,
                    messages=messages,
                )
                final_stop_reason = response.stop_reason

                if response.stop_reason == "end_turn":
                    text = next(
                        (b.text for b in response.content if hasattr(b, "text") and b.type == "text"),
                        "",
                    )
                    # Save the full turn (user msg + any tool exchanges + final reply)
                    # so Claude can reference tool results (e.g. UUIDs) in later turns.
                    turn_msgs = list(messages[len(history):])
                    turn_msgs.append({"role": "assistant", "content": text})
                    context.add_turn(group_jid, turn_msgs, max_pairs=blueprint.context_window)
                    return text

                if response.stop_reason == "max_tokens":
                    logger.warning(
                        "AgentRunner hit max_tokens | group=%s blueprint=%s turn_tools=%s",
                        group_jid, blueprint.id, [b.type for b in response.content],
                    )

                if response.stop_reason == "tool_use":
                    tc_list = [b for b in response.content if b.type == "tool_use"]
                    # Serialize SDK objects to plain dicts so messages is JSON-safe
                    # and can be stored in the context store.
                    messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

                    # Run all tool calls in parallel — independent calls in one turn
                    # are the common case and asyncio.gather cuts latency significantly.
                    async def _run_one(tc):
                        if tc.name not in allowed_tools:
                            logger.warning(
                                "Tool not permitted | group=%s tool=%s allowed=%s",
                                group_jid, tc.name, allowed_tools,
                            )
                            return f"Tool '{tc.name}' is not permitted for this agent."
                        raw = await self.registry.execute(
                            tc.name, tc.input,
                            group_jid=group_jid, sender=sender, is_admin=is_admin,
                            resolved_phone=sender_phone,
                            confirmation_store=confirmation_store,
                            multi_confirmation_store=multi_confirmation_store,
                        )
                        return raw if isinstance(raw, str) else json.dumps(raw)

                    result_texts = await asyncio.gather(*[_run_one(tc) for tc in tc_list])

                    tool_calls_made.extend([
                        {"name": tc.name, "preview": text[:120]}
                        for tc, text in zip(tc_list, result_texts)
                    ])
                    tool_results = [
                        {"type": "tool_result", "tool_use_id": tc.id, "content": text}
                        for tc, text in zip(tc_list, result_texts)
                    ]
                    messages.append({"role": "user", "content": tool_results})

            final_stop_reason = "max_tool_turns"
            fallback = "I reached my processing limit. Please try a simpler request."
            turn_msgs = list(messages[len(history):])
            turn_msgs.append({"role": "assistant", "content": fallback})
            context.add_turn(group_jid, turn_msgs, max_pairs=blueprint.context_window)
            return fallback

        except Exception as exc:
            error_text = str(exc)
            logger.exception("AgentRunner error | group=%s blueprint=%s", group_jid, blueprint.id)
            raise

        finally:
            duration_ms = int((time.monotonic() - start_ms) * 1000)
            logger.info(
                "AgentRunner done | group=%s stop=%s tools_called=%s duration_ms=%d",
                group_jid, final_stop_reason,
                [t["name"] for t in tool_calls_made], duration_ms,
            )
            self._write_log(
                group_jid=group_jid,
                blueprint_id=blueprint.id,
                sender_phone=sender_phone,
                history_pairs=len(history) // 2,
                tool_count=len(tool_schemas),
                tool_names=allowed_tools,
                stop_reason=final_stop_reason,
                tool_calls_made=tool_calls_made,
                error=error_text,
                duration_ms=duration_ms,
            )

    def _write_log(self, **kwargs) -> None:
        try:
            with SessionLocal() as db:
                db.add(RequestLog(
                    group_jid=kwargs["group_jid"],
                    blueprint_id=kwargs["blueprint_id"],
                    sender_phone=kwargs["sender_phone"],
                    history_pairs=kwargs["history_pairs"],
                    tool_count=kwargs["tool_count"],
                    tool_names=json.dumps(kwargs["tool_names"]),
                    stop_reason=kwargs["stop_reason"],
                    tool_calls_made=json.dumps(kwargs["tool_calls_made"]),
                    error=kwargs["error"],
                    duration_ms=kwargs["duration_ms"],
                ))
                db.commit()
        except Exception:
            logger.warning("Failed to write request log", exc_info=True)

    async def _commit_pending(self, mc) -> str:
        """Write a confirmed multi-party transaction to DB."""
        from app.db.session import SessionLocal
        from app.db.models import LedgerEntry, LedgerSettlement
        from app.tools.accounting_fifo import DebtLeg, apply_payment
        import uuid
        from datetime import date, timezone
        from decimal import Decimal

        p = mc.commit_params

        if mc.action == "commit_transaction":
            transaction_id = p["transaction_id"]
            group_jid = p["group_jid"]
            payer = p["payer_phone"]
            participants = p["participant_phones"]
            per_person = Decimal(str(p["per_person_ils"]))
            description = p["description"]
            tx_date = date.fromisoformat(p["transaction_date"])
            now = datetime.now(timezone.utc)

            with SessionLocal() as db:
                for phone in participants:
                    db.add(LedgerEntry(
                        transaction_id=transaction_id,
                        entry_type="debt",
                        group_jid=group_jid,
                        from_phone=phone,
                        to_phone=payer,
                        amount_ils=per_person,
                        amount_settled_ils=Decimal("0"),
                        description=description,
                        transaction_date=tx_date,
                        created_at=now,
                    ))
                db.commit()

            return (
                f"Transaction recorded: {payer} paid for {', '.join(participants)} — "
                f"{per_person:.2f} ILS each. (tx: {transaction_id[:8]})"
            )

        if mc.action == "commit_payment":
            group_jid = p["group_jid"]
            payer = p["payer_phone"]
            payee = p["payee_phone"]
            amount_ils = Decimal(str(p["amount_ils"]))
            pay_date = date.fromisoformat(p["payment_date"])
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
                    if row:
                        row.amount_settled_ils = new_settled
                payment_leg = LedgerEntry(
                    transaction_id=str(uuid.uuid4()),
                    entry_type="payment",
                    group_jid=group_jid,
                    from_phone=payer,
                    to_phone=payee,
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

            parts = [f"{amt:.2f} ILS off {did[:8]}" for did, amt in result.settlements]
            summary = "; ".join(parts) if parts else "no open debts found"
            return f"Payment of {amount_ils:.2f} ILS recorded. {summary}."

        return f"Unknown commit action: {mc.action}"
