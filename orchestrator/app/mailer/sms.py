"""Vibrate (vibrate.co.il) SMS delivery for WhatsApp-bridge reconnect alerts.

Israeli SMS gateway: one-time prepaid credit packages (no monthly fee,
messages never expire) instead of a per-number monthly rental — a better
fit than Twilio for a low-volume alert (at most one SMS per day, to one
number). REST API documented at
https://www.vibrate.co.il/vibrate-api-skill.md — raw calls via httpx,
matching this codebase's existing mailer style (no vendor SDK dependency).

Setup (done once in the Vibrate web app, not via this code):
  1. Create an account and buy a credit package.
  2. Approve a Sender ID (Settings) — an alphanumeric name or phone number;
     every send must reference one exactly, or the API rejects it.
  3. Create an access token (Settings -> Access Keys) and set
     VIBRATE_ACCESS_TOKEN / VIBRATE_SENDER in .env.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_VIBRATE_API_BASE = "https://api.vibrate.co.il"


async def send_sms(to: str, body: str) -> None:
    """Send a single SMS via Vibrate.

    Args:
        to: Destination phone number. Vibrate normalises Israeli numbers
            server-side (accepts "0501234567", "972501234567", etc.).
        body: Message text.

    Raises:
        RuntimeError: If Vibrate isn't configured, or the send fails.
    """
    if not (settings.vibrate_access_token and settings.vibrate_sender):
        raise RuntimeError(
            "Vibrate credentials not configured. Set VIBRATE_ACCESS_TOKEN and VIBRATE_SENDER."
        )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_VIBRATE_API_BASE}/v1/sms/send",
            headers={
                "Authorization": settings.vibrate_access_token,
                "Content-Type": "application/json",
            },
            json={"recipients": [to], "message": body, "sender": settings.vibrate_sender},
        )

    # Every Vibrate send endpoint returns exactly 202 (queued) on success —
    # never 200 — so anything else, including another 2xx, is a real failure.
    if resp.status_code != 202:
        raise RuntimeError(f"Vibrate SMS send failed ({resp.status_code}): {resp.text}")
    logger.info("SMS alert sent to %s", to)
