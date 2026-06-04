import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
import anthropic
import httpx

from app.config import settings
from app.db.session import SessionLocal
from app import seeder
from app.router import Router
from app.agent_runner import AgentRunner
from app.tool_registry import ToolRegistry
from app.command_handler import CommandHandler
from app.agent.context import ContextStore
from app.agent.confirmation import confirmation_store
from app.agent.multi_confirmation import multi_confirmation_store
from app.db.models import GroupParticipant
from app.participants import build_participant_block
from app.tools.invoice_tools import get_invoice_tools
from app.tools.accounting_tools import get_accounting_tools
from app.tools.automation_tools import get_automation_tools
from app.automation.executor import AutomationExecutor
from app.scheduler import start_scheduler, stop_scheduler, set_automation_executor
from app.pipeline.pipeline import process_image_event
from app.utils.rate_limiter import rate_limiter
from app.logging_config import configure_logging
from app.admin.router import router as admin_router, get_static_dir
from fastapi.staticfiles import StaticFiles

configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
if not _WEBHOOK_SECRET:
    logger.warning("WEBHOOK_SECRET is not configured — /webhook accepts unauthenticated requests")


def _upsert_participant(
    db,
    group_jid: str,
    phone: str,
    *,
    push_name: str | None = None,
    status: str = "active",
    removed_at=None,
) -> None:
    from datetime import datetime, timezone
    row = db.get(GroupParticipant, (group_jid, phone))
    if row is None:
        db.add(GroupParticipant(
            group_jid=group_jid,
            phone=phone,
            push_name=push_name,
            status=status,
            removed_at=removed_at,
        ))
    else:
        if status != row.status:
            row.status = status
        if removed_at is not None:
            row.removed_at = removed_at
        if push_name is not None and row.admin_name is None and row.push_name != push_name:
            row.push_name = push_name
    db.commit()


def _verify_webhook_auth(request: Request) -> None:
    if not _WEBHOOK_SECRET:
        return
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if token != _WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# NOTE: These action functions call _send(), which is defined later in this module.
# Python resolves names at call time, so this is safe at runtime.
async def _balance_summary_action(group_jid: str, db, config: dict) -> None:
    """Automation action: send an open-debt summary to the group."""
    from app.db.models import LedgerEntry
    from decimal import Decimal
    entries = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid).all()
    open_debts = [
        e for e in entries
        if (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > 0
    ]
    if not open_debts:
        await _send(group_jid, "Balance summary: No open debts — all settled! ✅")
        return
    lines = [
        f"• {e.from_phone} → {e.to_phone}: "
        f"₪{float(e.amount_ils - (e.amount_settled_ils or Decimal('0'))):.2f}"
        f" ({e.description})"
        for e in open_debts
    ]
    await _send(group_jid, "Balance summary:\n" + "\n".join(lines))


async def _monthly_invoice_report_action(group_jid: str, db, config: dict) -> None:
    """Automation action: send a monthly invoice summary to the group."""
    from app.db.models import Invoice
    from datetime import date
    today = date.today()
    first_of_month = today.replace(day=1)
    invoices = (
        db.query(Invoice)
        .filter(Invoice.group_id == group_jid, Invoice.invoice_date >= first_of_month)
        .all()
    )
    month_label = today.strftime("%B %Y")
    if not invoices:
        await _send(group_jid, f"Monthly report ({month_label}): No invoices this month.")
        return
    total = sum(float(inv.amount_ils or 0) for inv in invoices)
    lines = [
        f"• {inv.vendor or 'Unknown'}: ₪{float(inv.amount_ils or 0):.2f}"
        for inv in invoices
    ]
    await _send(
        group_jid,
        f"Monthly report ({month_label}) — {len(invoices)} invoices, ₪{total:.2f} total:\n"
        + "\n".join(lines),
    )


# --- Globals (initialized at startup) ---
router = Router()
command_handler = CommandHandler(bridge_url=settings.bridge_url)
context_store = ContextStore()
tool_registry = ToolRegistry()
agent_runner: AgentRunner | None = None
_http_client: Optional[httpx.AsyncClient] = None


class WebhookPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    jid: str
    sender: str
    message_id: str = Field(default="", alias="messageId")
    is_admin: bool = Field(default=False, alias="isAdmin")
    text: str | None = None
    image_base64: str | None = Field(default=None, alias="imageBase64")
    mime_type: str | None = Field(default=None, alias="mimeType")
    caption: str | None = None
    push_name: str | None = Field(default=None, alias="pushName")
    action: str | None = None
    participants: list[str] | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global agent_runner, _http_client

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required but not set")

    db = SessionLocal()
    seeder.seed(
        db,
        admin_phone=settings.admin_phone_number,
        legacy_group_jid=settings.legacy_group_jid or None,
    )
    db.close()

    anthropic_client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        max_retries=4,  # retry overload/529 up to 4 times with exponential backoff
    )
    _http_client = httpx.AsyncClient()

    tool_registry.register(get_invoice_tools())
    tool_registry.register(get_accounting_tools())
    tool_registry.register(get_automation_tools())

    automation_executor = AutomationExecutor()
    automation_executor.register_action("balance_summary", _balance_summary_action)
    automation_executor.register_action("monthly_invoice_report", _monthly_invoice_report_action)
    set_automation_executor(automation_executor)

    if settings.notion_api_key:
        from app.tools.notion_tools import get_notion_tools
        tool_registry.register(get_notion_tools(settings.notion_api_key, settings.notion_tasks_database_id))
    else:
        logger.warning("NOTION_API_KEY not set — Notion tools disabled")

    agent_runner = AgentRunner(anthropic_client, tool_registry)
    multi_confirmation_store.set_sender(
        lambda jid, text, mentions=None: _send(jid, text, mentions=mentions)
    )
    start_scheduler()

    logger.info("WhatsApp Agent Engine started — %d tools registered", len(tool_registry._tools))
    yield
    await _http_client.aclose()
    stop_scheduler()
    logger.info("Shutting down.")


app = FastAPI(title="WhatsApp Agent Engine", lifespan=lifespan)
app.include_router(admin_router, prefix="/admin")
app.mount("/admin/static", StaticFiles(directory=str(get_static_dir())), name="admin_static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, payload: WebhookPayload, background_tasks: BackgroundTasks):
    _verify_webhook_auth(request)
    background_tasks.add_task(_process, payload)
    return {"status": "ok"}


async def _process(payload: WebhookPayload) -> None:
    db = SessionLocal()
    try:
        logger.debug("Processing event: type=%s jid=%s", payload.type, payload.jid)

        # Track participant names passively from every incoming message
        if payload.push_name and payload.sender and payload.jid:
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            if sender_phone:
                try:
                    _upsert_participant(db, payload.jid, sender_phone, push_name=payload.push_name)
                except Exception:
                    db.rollback()
                    logger.debug("Could not upsert participant %s", sender_phone)

        # Handle participant join/leave events
        if payload.type == "participant_update":
            if payload.participants and payload.action in ("add", "remove", "leave"):
                from datetime import datetime, timezone
                for jid_str in payload.participants:
                    phone = jid_str.split("@")[0].split(":")[0]
                    if not phone:
                        continue
                    if payload.action == "add":
                        _upsert_participant(db, payload.jid, phone, status="active")
                    else:
                        _upsert_participant(db, payload.jid, phone,
                                            status="removed",
                                            removed_at=datetime.now(timezone.utc))
            return

        text = payload.text or payload.caption or ""

        # Commands are checked before blueprint lookup so /bind works on unregistered groups
        if command_handler.is_command(text):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            reply = await command_handler.handle(db, payload.jid, sender_phone, text)
            if reply:
                await _send(payload.jid, reply)
            return

        blueprint, entry = router.resolve(db, payload.jid)
        if blueprint is None:
            return

        participant_block = build_participant_block(db, payload.jid)

        # Rate limiting
        if payload.type == "image":
            if not rate_limiter.allow_image(payload.jid):
                logger.warning("Rate limit hit for group %s (image)", payload.jid)
                return
        else:
            if not rate_limiter.allow_text(payload.jid):
                logger.warning("Rate limit hit for group %s (text)", payload.jid)
                return

        if not router.check_trigger(entry, text=text, bot_phone=settings.bot_phone_number):
            return

        agent_message = text
        if payload.type == "image" and entry.blueprint_id == "invoice_curator":
            pipeline_result = await process_image_event({
                "jid": payload.jid,
                "sender": payload.sender,
                "messageId": payload.message_id,
                "imageBase64": payload.image_base64,
                "mimeType": payload.mime_type or "image/jpeg",
                "caption": payload.caption or "",
            })
            if "error" in pipeline_result:
                await _send(payload.jid, f"Pipeline error: {pipeline_result['error']}")
                return
            agent_message = _pipeline_result_to_message(pipeline_result)

        if not agent_message.strip():
            return

        reply = await agent_runner.run(
            blueprint=blueprint,
            group_jid=payload.jid,
            sender=payload.sender,
            is_admin=payload.is_admin,
            message=agent_message,
            context=context_store,
            confirmation_store=confirmation_store,
            multi_confirmation_store=multi_confirmation_store,
            custom_instructions=entry.custom_instructions,
            participant_block=participant_block,
        )
        await _send(payload.jid, reply)
    except Exception:
        logger.exception("Unhandled error processing event for group %s", payload.jid)
    finally:
        db.close()


def _pipeline_result_to_message(result: dict) -> str:
    if result.get("duplicate"):
        return f"Duplicate invoice detected: {result.get('vendor', '')} {result.get('invoice_number', '')}."
    parts = []
    if vendor := result.get("vendor"):
        parts.append(f"Vendor: {vendor}")
    if amount := result.get("amount_original"):
        currency = result.get("currency_original", "")
        parts.append(f"Amount: {amount} {currency}")
    if inv_date := result.get("invoice_date"):
        parts.append(f"Date: {inv_date}")
    if inv_num := result.get("invoice_number"):
        parts.append(f"Invoice #: {inv_num}")
    if result.get("flagged"):
        parts.append(f"Flagged: {result.get('flag_reason', '')}")
    return "New invoice received. " + " | ".join(parts) if parts else "New invoice received."


async def _send(jid: str, text: str, *, mentions: list[str] | None = None) -> None:
    if _http_client is None:
        logger.error("_send called before http client is initialised (jid=%s)", jid)
        return
    try:
        payload: dict = {"jid": jid, "text": text}
        if mentions:
            payload["mentions"] = mentions
        await _http_client.post(
            f"{settings.bridge_url}/send",
            json=payload,
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to send message to bridge for %s", jid)
