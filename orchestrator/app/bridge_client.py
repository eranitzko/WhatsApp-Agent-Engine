"""HTTP client for sending commands back to the Baileys bridge."""

import base64
import logging
import os

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Must match BRIDGE_SECRET in the bridge container.
_BRIDGE_SECRET: str = os.environ.get("BRIDGE_SECRET", "")


def _bridge_headers() -> dict:
    """Return auth headers for bridge requests."""
    if _BRIDGE_SECRET:
        return {"Authorization": f"Bearer {_BRIDGE_SECRET}"}
    return {}


async def send_text(jid: str, text: str) -> None:
    """Send a plain-text message to a WhatsApp JID."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                f"{settings.bridge_url}/send",
                json={"jid": jid, "text": text},
                headers=_bridge_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to send text to %s: %s", jid, exc)
            raise


async def send_file(jid: str, filename: str, mime_type: str, data: bytes) -> None:
    """Send a binary file (PDF, Excel, image) to a WhatsApp JID."""
    payload = {
        "jid": jid,
        "filename": filename,
        "mimeType": mime_type,
        "base64": base64.b64encode(data).decode(),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{settings.bridge_url}/send-file",
                json=payload,
                headers=_bridge_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to send file %s to %s: %s", filename, jid, exc)
            raise
