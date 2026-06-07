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
from app.db.models import GroupParticipant, CrossGroupConfirmation, SplitTransaction
from app.accounting.account_service import AccountService
from app.accounting.group_registration import GroupRegistrationHandler
from app.participants import build_participant_block
from app.tools.invoice_tools import get_invoice_tools
from app.tools.accounting_tools import get_accounting_tools
from app.tools.accounting_tools import set_account_service
from app.tools.split_tools import get_split_tools
from app.tools.split_tools import set_account_service as set_split_account_service
from app.tools.automation_tools import get_automation_tools
from app.export.tool import get_export_tools
from app.tools.send_email_tool import get_send_email_tools
from app.automation.executor import AutomationExecutor
from app.scheduler import start_scheduler, stop_scheduler, set_automation_executor
from app.pipeline.pipeline import process_image_event
from app.utils.rate_limiter import rate_limiter
from app.logging_config import configure_logging
from app.admin.router import router as admin_router, get_static_dir
from fastapi.staticfiles import StaticFiles
from app import bridge_client

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




# --- Globals (initialized at startup) ---
router = Router()
command_handler = CommandHandler(bridge_url=settings.bridge_url)
context_store = ContextStore()
tool_registry = ToolRegistry()
agent_runner: AgentRunner | None = None
account_service: AccountService = AccountService()
group_registration_handler: GroupRegistrationHandler = GroupRegistrationHandler()
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
    set_account_service(account_service)
    set_split_account_service(account_service)
    tool_registry.register(get_split_tools())
    tool_registry.register(get_automation_tools())
    tool_registry.register(get_export_tools())
    tool_registry.register(get_send_email_tools())

    from app import registry_ref
    registry_ref.set_registry(tool_registry)

    automation_executor = AutomationExecutor()
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
                bot_phone = settings.bot_phone_number or ""
                for jid_str in payload.participants:
                    phone = jid_str.split("@")[0].split(":")[0]
                    if not phone:
                        continue
                    if payload.action == "add":
                        _upsert_participant(db, payload.jid, phone, status="active")
                        # Check if the bot itself was added to a new group
                        if bot_phone and phone == bot_phone:
                            try:
                                meta = await bridge_client.fetch_group_meta(payload.jid)
                                human_phones = [
                                    p["jid"].split("@")[0].split(":")[0]
                                    for p in meta.get("participants", [])
                                    if p["jid"].split("@")[0].split(":")[0] != bot_phone
                                ]
                                await group_registration_handler.on_bot_added_to_group(
                                    db, payload.jid, human_phones
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to handle bot-join for group %s", payload.jid
                                )
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

        # Intercept sys-admin registration approvals
        if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n"):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            group_type = account_service.get_group_type(db, payload.jid)
            if group_type == "sys_admin":
                if group_registration_handler.is_pending_reply(db, payload.jid, text):
                    handled = await group_registration_handler.handle_admin_reply(
                        db, payload.jid, text
                    )
                    if handled:
                        return

        # Intercept cross-group confirmation replies (yes/no to 2nd-party transactions)
        if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n", "אישור", "ביטול"):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            resolved = account_service.handle_confirmation_reply(
                db, payload.jid, sender_phone, text
            )
            if resolved:
                # Find the confirmation we just resolved (most recent for this user)
                conf = (
                    db.query(CrossGroupConfirmation)
                    .filter(
                        CrossGroupConfirmation.target_phone == sender_phone,
                        CrossGroupConfirmation.target_group_jid == payload.jid,
                        CrossGroupConfirmation.status.in_(["confirmed", "rejected"]),
                    )
                    .order_by(CrossGroupConfirmation.created_at.desc())
                    .first()
                )
                if conf and conf.status == "confirmed":
                    if conf.split_transaction_id:
                        split = db.query(SplitTransaction).filter_by(
                            id=conf.split_transaction_id
                        ).first()
                        if split:
                            await account_service.finalize_split(db, split)
                    else:
                        await account_service.commit_confirmed_transaction(db, conf)
                elif conf and conf.status == "rejected":
                    if conf.split_transaction_id:
                        await account_service.handle_split_decline(db, conf)
                    else:
                        await bridge_client.send_message(
                            conf.initiator_group_jid,
                            "Your transaction was declined by the other party."
                        )
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
