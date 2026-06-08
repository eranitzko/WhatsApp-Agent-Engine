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


class AgentRunner:
    def __init__(self, client: anthropic.AsyncAnthropic, tool_registry: ToolRegistry):
        self.client = client
        self.registry = tool_registry

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
    ) -> str:
        start_ms = time.monotonic()
        allowed_tools = blueprint.tools_list()

        # Filter globally disabled tools (stored in SystemConfig["disabled_tools"])
        try:
            with SessionLocal() as _db:
                _row = _db.get(SystemConfig, "disabled_tools")
                if _row and _row.value:
                    _disabled = set(json.loads(_row.value))
                    allowed_tools = [t for t in allowed_tools if t not in _disabled]
        except Exception:
            logger.warning("Could not read disabled_tools from SystemConfig", exc_info=True)

        sender_phone = sender.split("@")[0].split(":")[0]

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

        # ── Single-action confirmation intercept ──────────────────────────────
        pending = confirmation_store.get(group_jid)
        if pending and not pending.is_expired():
            if confirmation_store.is_confirm(message):
                # DIAG: log exactly what action and params are being fired at confirmation time
                logger.info(
                    "DIAG confirmation fired | group=%s | action=%r | params_keys=%s | params=%s",
                    group_jid, pending.action,
                    list(pending.params.keys()),
                    {k: (v[:80] if isinstance(v, str) and len(v) > 80 else v)
                     for k, v in pending.params.items()},
                )
                result = await self.registry.execute(
                    pending.action, pending.params,
                    group_jid=group_jid, sender=sender, is_admin=is_admin,
                )
                confirmation_store.clear(group_jid)
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", str(result), max_pairs=blueprint.context_window)
                return str(result)
            elif confirmation_store.is_cancel(message):
                confirmation_store.clear(group_jid)
                reply = "Action cancelled."
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                return reply

        # ── Normal agent loop ─────────────────────────────────────────────────
        history = context.get_history(
            group_jid,
            max_pairs=blueprint.context_window,
            idle_minutes=blueprint.context_idle_reset_minutes,
        )
        messages = history + [{"role": "user", "content": message}]
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
                "text": f"Group-specific instructions:\n{custom_instructions}",
                "cache_control": {"type": "ephemeral"},
            })
        system.append({
            "type": "text",
            "text": f"Today's date: {datetime.now(timezone.utc).date()}. Sender is_admin: {is_admin}. Sender phone: {sender_phone}.",
        })
        tool_schemas = self.registry.get_schemas(allowed_tools)

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
                    context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                    context.add(group_jid, "assistant", text, max_pairs=blueprint.context_window)
                    return text

                if response.stop_reason == "max_tokens":
                    logger.warning(
                        "AgentRunner hit max_tokens | group=%s blueprint=%s turn_tools=%s",
                        group_jid, blueprint.id, [b.type for b in response.content],
                    )

                if response.stop_reason == "tool_use":
                    tc_list = [b for b in response.content if b.type == "tool_use"]
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []
                    for tc in tc_list:
                        if tc.name not in allowed_tools:
                            result_text = f"Tool '{tc.name}' is not permitted for this agent."
                            logger.warning(
                                "Tool not permitted | group=%s tool=%s allowed=%s",
                                group_jid, tc.name, allowed_tools,
                            )
                        else:
                            raw = await self.registry.execute(
                                tc.name, tc.input,
                                group_jid=group_jid, sender=sender, is_admin=is_admin,
                                confirmation_store=confirmation_store,
                                multi_confirmation_store=multi_confirmation_store,
                            )
                            result_text = raw if isinstance(raw, str) else json.dumps(raw)
                        tool_calls_made.append({
                            "name": tc.name,
                            "preview": result_text[:120],
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": result_text,
                        })
                    messages.append({"role": "user", "content": tool_results})

            final_stop_reason = "max_tool_turns"
            fallback = "I reached my processing limit. Please try a simpler request."
            context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
            context.add(group_jid, "assistant", fallback, max_pairs=blueprint.context_window)
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
