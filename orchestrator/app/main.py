import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
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
from app.tools.invoice_tools import get_invoice_tools
from app.tools.notion_tools import get_notion_tools
from app.pipeline.pipeline import process_image_event
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# --- Globals (initialized at startup) ---
router = Router()
command_handler = CommandHandler()
context_store = ContextStore()
tool_registry = ToolRegistry()
agent_runner: AgentRunner | None = None


class WebhookPayload(BaseModel):
    type: str
    jid: str
    sender: str
    message_id: str = ""
    is_admin: bool = False
    text: str | None = None
    image_base64: str | None = None
    mime_type: str | None = None
    caption: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_runner

    db = SessionLocal()
    seeder.seed(
        db,
        admin_phone=settings.admin_phone_number,
        legacy_group_jid=settings.legacy_group_jid or None,
    )
    db.close()

    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    tool_registry.register(get_invoice_tools())
    tool_registry.register(get_notion_tools(settings.notion_api_key, settings.notion_tasks_database_id))

    agent_runner = AgentRunner(anthropic_client, tool_registry)

    logger.info("WhatsApp Agent Engine started — %d tools registered", len(tool_registry._tools))
    yield
    logger.info("Shutting down.")


app = FastAPI(title="WhatsApp Agent Engine", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    background_tasks.add_task(_process, payload)
    return {"status": "ok"}


async def _process(payload: WebhookPayload) -> None:
    db = SessionLocal()
    try:
        blueprint, entry = router.resolve(db, payload.jid)
        if blueprint is None:
            return

        text = payload.text or payload.caption or ""

        if command_handler.is_command(text):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            reply = await command_handler.handle(db, payload.jid, sender_phone, text)
            if reply:
                await _send(payload.jid, reply)
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

        reply = await agent_runner.run(
            blueprint=blueprint,
            group_jid=payload.jid,
            sender=payload.sender,
            is_admin=payload.is_admin,
            message=agent_message,
            context=context_store,
            confirmation_store=confirmation_store,
        )
        await _send(payload.jid, reply)
    except Exception:
        logger.exception("Unhandled error processing event for group %s", payload.jid)
    finally:
        db.close()


def _pipeline_result_to_message(result: dict) -> str:
    """Convert pipeline result dict to a human-readable agent_message string."""
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
        parts.append(f"⚠️ Flagged: {result.get('flag_reason', '')}")
    return "New invoice received. " + " | ".join(parts) if parts else "New invoice received."


async def _send(jid: str, text: str) -> None:
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{settings.bridge_url}/send",
                json={"jid": jid, "text": text},
                timeout=10,
            )
        except Exception:
            logger.exception("Failed to send message to bridge for %s", jid)
