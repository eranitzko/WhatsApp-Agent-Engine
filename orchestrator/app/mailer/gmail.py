"""Gmail SMTP email delivery for invoice reports and system notifications.

Uses smtplib with an App Password (OAuth-free, single credential).
Attachments are sent as binary MIME parts.
"""

from __future__ import annotations

import io
import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_report_email(
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, str, bytes]],  # (filename, mime_type, data)
) -> None:
    """Send an email with one or more attachments via Gmail SMTP.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
        attachments: List of (filename, mime_type, bytes) tuples.

    Raises:
        RuntimeError: If sending fails.
    """
    if not settings.gmail_user or not settings.gmail_app_password:
        raise RuntimeError(
            "Gmail credentials not configured. Set GMAIL_USER and GMAIL_APP_PASSWORD."
        )

    msg = MIMEMultipart()
    msg["From"]    = settings.gmail_user
    msg["To"]      = to
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain", "utf-8"))

    for filename, mime_type, data in attachments:
        main_type, sub_type = mime_type.split("/", 1)
        part = MIMEBase(main_type, sub_type)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.gmail_user, settings.gmail_app_password)
            server.sendmail(settings.gmail_user, to, msg.as_bytes())
        logger.info("Report email sent to %s (subject: %s)", to, subject)
    except smtplib.SMTPException as exc:
        logger.error("Gmail SMTP error: %s", exc)
        raise RuntimeError(f"Failed to send email: {exc}") from exc


def send_qr_email(qr_string: str) -> None:
    """Send the WhatsApp re-auth QR code as an inline image to GMAIL_USER.

    Args:
        qr_string: Raw QR string provided by Baileys.

    Raises:
        RuntimeError: If sending fails.
    """
    import qrcode  # local import — only needed for this function

    if not settings.gmail_user or not settings.gmail_app_password:
        raise RuntimeError("Gmail credentials not configured.")

    img = qrcode.make(qr_string)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    msg = MIMEMultipart("related")
    msg["From"]    = settings.gmail_user
    msg["To"]      = settings.gmail_user
    msg["Subject"] = "⚠️ Invoice Curator — WhatsApp QR scan needed"

    html = (
        "<p>The WhatsApp session expired. Scan the QR code below to reconnect:</p>"
        '<img src="cid:qrcode" style="width:300px;height:300px;" />'
        "<p>Or tail logs: <code>fly logs --app invoice-curator</code></p>"
    )
    msg.attach(MIMEText(html, "html", "utf-8"))

    img_part = MIMEImage(png_bytes, "png")
    img_part.add_header("Content-ID", "<qrcode>")
    img_part.add_header("Content-Disposition", "inline", filename="qr.png")
    msg.attach(img_part)

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.gmail_user, settings.gmail_app_password)
            server.sendmail(settings.gmail_user, settings.gmail_user, msg.as_bytes())
        logger.info("QR notification email sent to %s", settings.gmail_user)
    except smtplib.SMTPException as exc:
        logger.error("Gmail SMTP error sending QR notification: %s", exc)
        raise RuntimeError(f"Failed to send QR email: {exc}") from exc
