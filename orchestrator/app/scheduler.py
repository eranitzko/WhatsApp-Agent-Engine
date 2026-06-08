"""APScheduler — dispatches due ScheduledMessages, fires automation rules,
and expires stale multi-confirmations."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.session import SessionLocal
from app.automation.evaluators import ThresholdEvaluator
from app import bridge_client

if TYPE_CHECKING:
    from app.automation.executor import AutomationExecutor

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_BRIDGE_SECRET: str = os.environ.get("BRIDGE_SECRET", "")

# Set at startup by main.py via set_automation_executor()
_automation_executor: "AutomationExecutor | None" = None


def set_automation_executor(executor: "AutomationExecutor") -> None:
    global _automation_executor
    _automation_executor = executor


def _bridge_headers() -> dict:
    return {"Authorization": f"Bearer {_BRIDGE_SECRET}"} if _BRIDGE_SECRET else {}


# ── Existing jobs ─────────────────────────────────────────────────────────────

async def _expire_multi_confirmations() -> None:
    """Cancel timed-out multi-party confirmations and notify their groups."""
    from app.agent.multi_confirmation import multi_confirmation_store
    expired = multi_confirmation_store.drain_expired()
    for mc in expired:
        timed_out_phones = [p for p, done in mc.awaiting.items() if not done]
        timed_out_str = ", ".join(f"@{p}" for p in timed_out_phones)
        msg = (
            f"Transaction cancelled — {timed_out_str} did not confirm in time.\n"
            f"{mc.description}"
        )
        mentions = [f"{p}@s.whatsapp.net" for p in timed_out_phones]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{settings.bridge_url}/send",
                    json={"jid": mc.group_jid, "text": msg, "mentions": mentions},
                    headers=_bridge_headers(),
                )
            logger.info("Sent expiry notice for mc %s to %s", mc.id, mc.group_jid)
        except Exception:
            logger.exception("Failed to send expiry notice for mc %s to %s", mc.id, mc.group_jid)


async def _dispatch_due_messages() -> None:
    """Query due scheduled messages, send each via bridge, mark sent."""
    from app.db.models import ScheduledMessage
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = (
            db.query(ScheduledMessage)
            .filter(ScheduledMessage.sent == False, ScheduledMessage.send_at <= now)
            .all()
        )
        for msg in due:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{settings.bridge_url}/send",
                        json={"jid": msg.group_jid, "text": msg.message},
                        headers=_bridge_headers(),
                    )
                msg.sent = True
                logger.info("Dispatched scheduled message %s to %s", msg.id, msg.group_jid)
            except Exception:
                logger.exception("Failed to dispatch scheduled message %s", msg.id)
        db.commit()


# ── Automation jobs ───────────────────────────────────────────────────────────

async def _fire_recurring_rules() -> None:
    """Fire recurring and one_off automation rules that are due."""
    from app.db.models import AutomationRule
    from croniter import croniter

    if _automation_executor is None:
        logger.warning("_fire_recurring_rules: no automation executor configured")
        return

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type.in_(["recurring", "one_off"]),
            )
            .all()
        )
        for rule in rules:
            if not rule.schedule_cron:
                continue
            # Don't fire again within the same 60-min window (restart/misfire guard)
            if rule.last_fired_at:
                last_check = rule.last_fired_at
                if last_check.tzinfo is None:
                    last_check = last_check.replace(tzinfo=timezone.utc)
                secs_since = (now - last_check).total_seconds()
                if secs_since < 3600:
                    # DIAG: log skipped rule so we can see why it didn't fire on time
                    logger.info(
                        "DIAG scheduler skip | rule=%s %r | last_fired=%s | secs_since=%.0f < 3600",
                        rule.id, rule.name, rule.last_fired_at, secs_since,
                    )
                    continue
            try:
                if rule.rule_type == "one_off":
                    fire_at = datetime.fromisoformat(rule.schedule_cron)
                    if fire_at.tzinfo is None:
                        fire_at = fire_at.replace(tzinfo=timezone.utc)
                    if fire_at > now:
                        # DIAG: log future one_off that isn't due yet
                        logger.info(
                            "DIAG scheduler not-yet | rule=%s %r | fire_at=%s | now=%s | delta=%.0fs",
                            rule.id, rule.name, fire_at, now, (fire_at - now).total_seconds(),
                        )
                        continue
                else:  # recurring
                    base = now - timedelta(hours=1)
                    itr = croniter(rule.schedule_cron, base)
                    next_dt = itr.get_next(datetime)
                    if next_dt > now:
                        # DIAG: log recurring rule that isn't due in this window
                        logger.info(
                            "DIAG scheduler not-due | rule=%s %r | cron=%r | window=[%s, %s] | next_due=%s",
                            rule.id, rule.name, rule.schedule_cron, base, now, next_dt,
                        )
                        continue
            except Exception:
                logger.exception(
                    "Invalid schedule_cron for rule %s: %r", rule.id, rule.schedule_cron
                )
                continue

            # DIAG: log that we're about to fire this rule, with timing details
            logger.info(
                "DIAG scheduler FIRING | rule=%s %r | type=%s | cron=%r | last_fired=%s | now=%s",
                rule.id, rule.name, rule.rule_type, rule.schedule_cron, rule.last_fired_at, now,
            )
            rule.last_fired_at = now
            if rule.rule_type == "one_off":
                rule.status = "done"
            db.commit()

            await _automation_executor.execute(rule, db)
            logger.info("Fired automation rule %s (%s)", rule.id, rule.name)


async def _check_inactivity() -> None:
    """Fire inactivity rules for groups that have been silent long enough."""
    from app.db.models import AutomationRule, ConversationHistory

    if _automation_executor is None:
        logger.warning("_check_inactivity: no automation executor configured")
        return

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type == "inactivity",
            )
            .all()
        )
        for rule in rules:
            if not rule.inactivity_hours:
                continue
            # Don't re-fire within the same inactivity window
            if rule.last_fired_at:
                last_fired = rule.last_fired_at
                if last_fired.tzinfo is None:
                    last_fired = last_fired.replace(tzinfo=timezone.utc)
                if (now - last_fired).total_seconds() / 3600 < rule.inactivity_hours:
                    continue
            history = db.query(ConversationHistory).filter_by(group_id=rule.group_jid).first()
            if history is None:
                continue
            last_active = history.last_active
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            silence_hours = (now - last_active).total_seconds() / 3600
            if silence_hours < rule.inactivity_hours:
                continue

            rule.last_fired_at = now
            db.commit()
            await _automation_executor.execute(rule, db)
            logger.info(
                "Fired inactivity rule %s for %s (%.1fh silence)",
                rule.id, rule.group_jid, silence_hours,
            )


async def _evaluate_thresholds() -> None:
    """Fire threshold rules whose metric condition is met (max once per 24h)."""
    from app.db.models import AutomationRule
    import json as _json

    if _automation_executor is None:
        logger.warning("_evaluate_thresholds: no automation executor configured")
        return

    now = datetime.now(timezone.utc)
    evaluator = ThresholdEvaluator()
    _OPS = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
    }

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type == "threshold",
            )
            .all()
        )
        for rule in rules:
            # Skip if already fired within the last 24 hours
            if rule.last_fired_at:
                last_fired = rule.last_fired_at
                if last_fired.tzinfo is None:
                    last_fired = last_fired.replace(tzinfo=timezone.utc)
                hours_since = (now - last_fired).total_seconds() / 3600
                if hours_since < 24:
                    continue
            if not rule.threshold_config:
                continue
            try:
                tc = _json.loads(rule.threshold_config)
                metric = tc["metric"]
                op = tc["op"]
                target = float(tc["value"])
                actual = evaluator.evaluate(db, rule.group_jid, metric)
                op_fn = _OPS.get(op)
                if op_fn is None or not op_fn(actual, target):
                    continue
            except Exception:
                logger.exception("Failed to evaluate threshold for rule %s", rule.id)
                continue

            rule.last_fired_at = now
            db.commit()
            await _automation_executor.execute(rule, db)
            logger.info(
                "Fired threshold rule %s (%s %s %s, actual=%.2f)",
                rule.id, metric, op, target, actual,
            )


async def _expire_cross_group_confirmations() -> None:
    """Flip pending cross-group confirmations past their expiry to timed_out and notify parties."""
    from app.db.models import CrossGroupConfirmation, SplitTransaction

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        expired = (
            db.query(CrossGroupConfirmation)
            .filter(
                CrossGroupConfirmation.status == "pending",
                CrossGroupConfirmation.expires_at <= now,
            )
            .all()
        )
        for conf in expired:
            conf.status = "timed_out"
        db.commit()

        for conf in expired:
            # If part of a split, check if we should suspend it
            if conf.split_transaction_id:
                split = db.query(SplitTransaction).filter_by(
                    id=conf.split_transaction_id, status="pending"
                ).first()
                if split:
                    split.status = "suspended"
                    db.query(CrossGroupConfirmation).filter_by(
                        split_transaction_id=split.id, status="pending"
                    ).update({"status": "paused"})
                    db.commit()
                    msg = (
                        f"The split ({split.description}, ₪{float(split.total_amount):.2f}) "
                        f"was not confirmed in time and has been suspended."
                    )
                    try:
                        await bridge_client.send_message(split.reporter_group_jid, msg)
                    except Exception:
                        logger.exception("Failed to notify split reporter %s", split.reporter_group_jid)
            else:
                # Standalone 2-party confirmation timeout
                msg = (
                    f"A transaction confirmation timed out and was not recorded "
                    f"({conf.action_type})."
                )
                for jid in {conf.initiator_group_jid, conf.target_group_jid}:
                    try:
                        await bridge_client.send_message(jid, msg)
                    except Exception:
                        logger.exception("Failed to send timeout notice to %s", jid)


async def _expire_split_transactions() -> None:
    """Suspend pending splits where all confirmations have timed out."""
    from app.db.models import CrossGroupConfirmation, SplitTransaction

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        pending_splits = db.query(SplitTransaction).filter_by(status="pending").all()
        for split in pending_splits:
            confs = db.query(CrossGroupConfirmation).filter_by(
                split_transaction_id=split.id
            ).all()
            all_resolved = all(
                c.status in ("confirmed", "self_confirmed", "rejected", "timed_out", "paused")
                for c in confs
            )
            if not all_resolved:
                continue
            any_timed_out = any(
                c.status == "timed_out" for c in confs
            )
            if any_timed_out:
                split.status = "suspended"
        db.commit()


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def start_scheduler() -> None:
    _scheduler.add_job(_dispatch_due_messages, "interval", seconds=60, id="dispatch_messages")
    _scheduler.add_job(
        _expire_multi_confirmations, "interval", seconds=60, id="expire_multi_confirmations"
    )
    _scheduler.add_job(
        _fire_recurring_rules, "interval", minutes=60, id="fire_recurring_rules"
    )
    _scheduler.add_job(
        _check_inactivity, "interval", minutes=60, id="check_inactivity"
    )
    _scheduler.add_job(
        _evaluate_thresholds, "interval", minutes=60, id="evaluate_thresholds"
    )
    _scheduler.add_job(
        _expire_cross_group_confirmations, "interval", minutes=60, id="expire_cross_group_confirmations"
    )
    _scheduler.add_job(
        _expire_split_transactions, "interval", minutes=60, id="expire_split_transactions"
    )
    _scheduler.start()
    logger.info("APScheduler started — 2 × 60s jobs, 5 × 60min automation jobs")


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
