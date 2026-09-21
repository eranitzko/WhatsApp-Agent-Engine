"""send_sms — ToolRegistry tool for sending a plain-text SMS via Vibrate.

Params:
  to:      str — recipient phone number (E.164-ish digits, e.g. "972501234567")
  message: str — SMS text (up to 256 chars = 1 credit; longer costs more)

Admin only. Unlike every other message this bot sends, SMS reaches the
recipient's phone directly over the cellular network, outside WhatsApp,
and costs real prepaid credit (app.mailer.sms, Vibrate gateway) — so it's
gated the same way send_email is, not left open to every group member.
"""

from __future__ import annotations

import logging

from app.mailer.sms import send_sms

logger = logging.getLogger(__name__)


async def _exec_send_sms(params: dict, **ctx) -> str:
    if not ctx.get("is_admin", False):
        return "send_sms is admin only."

    to = params.get("to", "").strip()
    message = params.get("message", "").strip()

    if not to:
        return "Missing 'to' phone number."
    if not message:
        return "Missing 'message'."

    try:
        await send_sms(to, message)
    except RuntimeError as exc:
        logger.exception("send_sms tool: Vibrate send failed")
        return f"Failed to send SMS: {exc}"

    return f"SMS sent to {to}."


_SCHEMA = {
    "name": "send_sms",
    "category": "export",
    "access": "admin",
    "description": (
        "Sends a plain-text SMS to a phone number via the Vibrate SMS gateway. Admin only. "
        "Unlike every other message this bot sends, this reaches the recipient's phone "
        "directly over the cellular network, outside WhatsApp — use it when WhatsApp delivery "
        "can't be relied on (e.g. the recipient hasn't set up the bot, or WhatsApp itself is "
        "down), not as a general substitute for a normal reply. Costs one prepaid SMS credit "
        "per message (up to 256 characters, more for longer messages) — the remaining balance "
        "is shown in the admin panel's Mobile tab."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number, e.g. 972501234567."},
            "message": {"type": "string", "description": "SMS text."},
        },
        "required": ["to", "message"],
    },
}


def get_send_sms_tools() -> dict[str, dict]:
    return {"send_sms": {"schema": _SCHEMA, "executor": _exec_send_sms}}
