"""Twilio SMS delivery for WhatsApp-bridge reconnect alerts.

Raw REST calls (Basic Auth over httpx), matching this codebase's existing
mailer style (smtplib, not a heavier vendor SDK) — no new dependency needed.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


async def send_sms(to: str, body: str) -> None:
    """Send a single SMS via Twilio.

    Args:
        to: Destination phone number (E.164, e.g. "+972501234567").
        body: Message text.

    Raises:
        RuntimeError: If Twilio isn't configured, or the send fails.
    """
    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number):
        raise RuntimeError(
            "Twilio credentials not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER."
        )

    url = f"{_TWILIO_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            data={"To": to, "From": settings.twilio_from_number, "Body": body},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )

    if resp.status_code >= 300:
        raise RuntimeError(f"Twilio SMS send failed ({resp.status_code}): {resp.text}")
    logger.info("SMS alert sent to %s", to)
