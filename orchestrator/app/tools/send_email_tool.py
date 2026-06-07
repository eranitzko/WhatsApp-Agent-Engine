"""send_email — ToolRegistry tool for sending custom emails with template support.

Params:
  to:      str — recipient address (may contain {{variables}})
  subject: str — email subject (may contain {{variables}})
  body:    str — plain-text body (may contain {{variables}})

All template variables are resolved via WorkflowContext at send time using
a fresh DB session. This means the tool works correctly whether called:
  - Directly from a workflow step (templates may already be resolved)
  - As a confirmed action after request_confirmation (resolves from DB fresh)

Admin only. Validates recipient against REPORT_EMAIL_ALLOWLIST.
"""

from __future__ import annotations

import asyncio
import logging

from app.mailer.gmail import send_report_email
from app.automation.context import WorkflowContext

logger = logging.getLogger(__name__)


def _is_allowed(to: str, db=None) -> bool:
    """Return True if `to` is permitted to receive emails.

    Reads from the email_allowlist DB table.
    If the table is empty, any address is allowed (open by default).
    The `db` parameter is injected in tests; production uses a fresh session.
    """
    from app.db.models import EmailAllowlist

    def _check(session):
        count = session.query(EmailAllowlist).count()
        if count == 0:
            return True
        return (
            session.query(EmailAllowlist)
            .filter(EmailAllowlist.email == to.strip().lower())
            .first()
        ) is not None

    if db is not None:
        return _check(db)

    from app.db.session import SessionLocal
    with SessionLocal() as session:
        return _check(session)


async def _exec_send_email(params: dict, **ctx) -> str:
    if not ctx.get("is_admin", False):
        return "send_email is admin only."

    group_jid: str = ctx.get("group_jid", "")

    to = params.get("to", "").strip()
    subject = params.get("subject", "").strip()
    body = params.get("body", "").strip()

    if not to:
        return "Missing 'to' email address."
    if not subject:
        return "Missing 'subject'."
    if not body:
        return "Missing 'body'."

    # Resolve templates at send time using a fresh DB session
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        wf_ctx = WorkflowContext(group_jid, db=db)
        to = wf_ctx.resolve(to)
        subject = wf_ctx.resolve(subject)
        body = wf_ctx.resolve(body)

    if not _is_allowed(to):
        return (
            f"Email address '{to}' is not in the allowed recipient list. "
            f"Add it to REPORT_EMAIL_ALLOWLIST to permit it."
        )

    try:
        await asyncio.to_thread(
            send_report_email,
            to=to,
            subject=subject,
            body=body,
            attachments=[],
        )
    except Exception as exc:
        logger.exception("send_email: Gmail send failed")
        return f"Failed to send email: {exc}"

    return f"Email sent to {to}."


_SCHEMA = {
    "name": "send_email",
    "category": "export",
    "description": (
        "Send a custom plain-text email. Admin only. "
        "All fields support {{variable}} templates resolved at send time: "
        "{{previous_month}}, {{previous_month_invoice_total}}, {{monthly_invoice_total}}, "
        "{{open_debt_amount}}, {{today}}, {{current_month}}, {{previous_month_name}}, "
        "{{previous_month_year}}, plus outputs of earlier workflow steps via their output_key."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to":      {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject. Supports {{variables}}."},
            "body":    {"type": "string", "description": "Plain-text email body. Supports {{variables}}."},
        },
        "required": ["to", "subject", "body"],
    },
}


def get_send_email_tools() -> dict[str, dict]:
    return {"send_email": {"schema": _SCHEMA, "executor": _exec_send_email}}
