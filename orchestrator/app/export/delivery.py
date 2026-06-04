"""Generic file delivery: WhatsApp group and/or email."""

from __future__ import annotations

import asyncio
import logging

from app.bridge_client import send_file
from app.mailer.gmail import send_report_email

logger = logging.getLogger(__name__)


async def deliver_files(
    group_jid: str,
    email: str | None,
    delivery: str,
    files: list[tuple[str, str, bytes]],
    subject: str = "Report",
    body: str = "Please find the report attached.",
) -> None:
    """Deliver a list of (filename, mime_type, bytes) via group and/or email.

    delivery: "group" | "email" | "both"
    Raises on delivery failure so the caller can surface the error.
    """
    tasks = []

    if delivery in ("group", "both"):
        for filename, mime, data in files:
            tasks.append(send_file(group_jid, filename, mime, data))

    if delivery in ("email", "both") and email:
        tasks.append(
            asyncio.to_thread(
                send_report_email,
                to=email,
                subject=subject,
                body=body,
                attachments=files,
            )
        )

    if tasks:
        await asyncio.gather(*tasks)
