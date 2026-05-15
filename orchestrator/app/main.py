import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.agent.agent import agent
from app.db.session import init_db
from app.logging_config import configure_logging
from app.pipeline.pipeline import process_image_event
from app.utils.rate_limiter import rate_limiter

configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Shared secret that the bridge must supply as "Authorization: Bearer <secret>".
# If not set, a startup warning is logged but requests are still accepted so that
# local dev without secrets keeps working.
_WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
if not _WEBHOOK_SECRET:
    logger.warning(
        "WEBHOOK_SECRET is not configured — /webhook accepts unauthenticated requests"
    )


def _verify_webhook_auth(request: Request) -> None:
    """Raise HTTP 401 if the bearer token does not match WEBHOOK_SECRET."""
    if not _WEBHOOK_SECRET:
        return  # secret not configured; skip check
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if token != _WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting Invoice Curator backend...")
    init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(title="Invoice Curator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/internal/qr-notify")
async def qr_notify(request: Request, background_tasks: BackgroundTasks):
    """Called by the bridge when a WhatsApp QR code is generated. Emails it to the admin."""
    _verify_webhook_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    qr_string = body.get("qr", "")
    if not qr_string:
        raise HTTPException(status_code=400, detail="Missing qr field")

    from app.mailer.gmail import send_qr_email
    background_tasks.add_task(send_qr_email, qr_string)
    return {"ok": True}


async def _handle_image(event: dict) -> None:
    """Background task: run invoice pipeline then let the agent respond."""
    try:
        result = await process_image_event(event)
        await agent.handle_image_event({**event, "extraction": result})
    except Exception:
        logger.exception("Unhandled error in image pipeline for group %s", event.get("jid"))


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive events from the Baileys bridge."""
    _verify_webhook_auth(request)
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type")
    logger.debug("Received event: type=%s jid=%s", event_type, event.get("jid"))

    jid = event.get("jid", "")

    match event_type:
        case "text":
            if not rate_limiter.allow_text(jid):
                logger.warning("Rate limit hit for group %s (text)", jid)
                return {"ok": True, "rate_limited": True}
            else:
                await agent.handle_text_event(event)
        case "image":
            if not rate_limiter.allow_image(jid):
                logger.warning("Rate limit hit for group %s (image)", jid)
                return {"ok": True, "rate_limited": True}
            else:
                # Return 200 immediately — pipeline runs in background
                background_tasks.add_task(_handle_image, event)
        case _:
            logger.warning("Unknown event type: %s", event_type)

    return {"ok": True}
