"""APScheduler — dispatches due ScheduledMessages and expires stale multi-confirmations."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_BRIDGE_SECRET: str = os.environ.get("BRIDGE_SECRET", "")


def _bridge_headers() -> dict:
    return {"Authorization": f"Bearer {_BRIDGE_SECRET}"} if _BRIDGE_SECRET else {}


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
    from app.db.session import SessionLocal
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


def start_scheduler() -> None:
    _scheduler.add_job(_dispatch_due_messages, "interval", seconds=60, id="dispatch_messages")
    _scheduler.add_job(_expire_multi_confirmations, "interval", seconds=60, id="expire_multi_confirmations")
    _scheduler.start()
    logger.info("APScheduler started — polling every 60s")


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
