"""Report generation orchestrator.

Called from the agent tool executor for generate_report and exec_send_email.
Handles PDF, Excel, or both formats; image appendix; WA delivery; email delivery.
"""

from __future__ import annotations

import logging
from calendar import month_name
from datetime import datetime, timezone

from app.bridge_client import send_file, send_text
from app.config import settings
from app.db.models import GroupConfig
from app.db.session import SessionLocal
from app.pipeline.storage import download_image_sync
from datetime import date as _date

from app.reports.data import fetch_report_data
from app.reports.excel_report import generate_excel
from app.reports.pdf_report import generate_pdf

logger = logging.getLogger(__name__)


def _get_config(group_id: str) -> GroupConfig:
    with SessionLocal() as db:
        config = db.get(GroupConfig, group_id)
        if not config:
            config = GroupConfig(group_id=group_id)
            db.add(config)
            db.commit()
            db.refresh(config)
        db.expunge(config)
        return config


async def generate_and_send_report(
    group_id: str,
    month: int = None,
    year: int = None,
    fmt: str = "pdf",
    attach_images: bool = False,
    force_dual_currency: bool | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Generate report(s) and send to the WhatsApp group.

    Returns {"ok": True, "sent": [...]} or {"error": str}.
    """
    config = _get_config(group_id)
    lang   = config.feedback_language
    title  = config.report_header or None
    author = config.report_author or None

    dual_override = force_dual_currency if force_dual_currency is not None else config.force_dual_currency

    sd = _date.fromisoformat(start_date) if start_date else None
    ed = _date.fromisoformat(end_date)   if end_date   else None

    try:
        data = fetch_report_data(
            group_id, month, year,
            force_dual_currency=dual_override,
            start_date=sd, end_date=ed,
        )
    except Exception as exc:
        logger.exception("Failed to fetch report data: %s", exc)
        return {"error": f"Failed to query invoices: {exc}"}

    if not data.rows:
        period_desc = data.period_label or f"{month_name[data.month]} {data.year}"
        return {"error": f"No invoices found for {period_desc}."}

    period   = data.period_label or f"{month_name[data.month]}_{data.year}"
    sent     = []
    errors   = []

    if fmt in ("pdf", "both"):
        try:
            loader = download_image_sync if attach_images else None
            pdf_bytes = generate_pdf(
                data,
                lang=lang,
                title=title,
                author=author,
                attach_images=attach_images,
                image_loader=loader,
            )
            filename = f"invoices_{period}.pdf"
            await send_file(group_id, filename, "application/pdf", pdf_bytes)
            sent.append(filename)
        except Exception as exc:
            logger.exception("PDF generation failed: %s", exc)
            errors.append(f"PDF failed: {exc}")

    if fmt in ("excel", "both"):
        try:
            xlsx_bytes = generate_excel(data, lang=lang, title=title, author=author)
            filename = f"invoices_{period}.xlsx"
            await send_file(
                group_id,
                filename,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                xlsx_bytes,
            )
            sent.append(filename)
        except Exception as exc:
            logger.exception("Excel generation failed: %s", exc)
            errors.append(f"Excel failed: {exc}")

    if errors and not sent:
        return {"error": "; ".join(errors)}

    result = {"ok": True, "sent": sent, "invoice_count": len(data.rows)}
    if errors:
        result["warnings"] = errors
    return result


async def generate_and_email_report(
    group_id: str,
    month: int = None,
    year: int = None,
    to_email: str = "",
    fmt: str = "pdf",
    attach_images: bool = False,
    force_dual_currency: bool | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Generate report(s) and send by email.

    Returns {"ok": True, "sent_to": email} or {"error": str}.
    """
    from app.mailer.gmail import send_report_email

    config = _get_config(group_id)
    lang   = config.feedback_language
    title  = config.report_header or None
    author = config.report_author or None

    dual_override = force_dual_currency if force_dual_currency is not None else config.force_dual_currency

    sd = _date.fromisoformat(start_date) if start_date else None
    ed = _date.fromisoformat(end_date)   if end_date   else None

    try:
        data = fetch_report_data(
            group_id, month, year,
            force_dual_currency=dual_override,
            start_date=sd, end_date=ed,
        )
    except Exception as exc:
        return {"error": f"Failed to query invoices: {exc}"}

    if not data.rows:
        period_desc = data.period_label or f"{month_name[data.month]} {data.year}"
        return {"error": f"No invoices found for {period_desc}."}

    period  = data.period_label or f"{month_name[data.month]} {data.year}"
    subject = f"{title or 'Invoice Report'} — {period}"
    body    = (
        f"Please find attached the invoice report for {period}.\n"
        f"Invoices: {len(data.rows)} | Total: ₪{float(data.total_ils):,.2f}"
    )

    safe_period = period.replace("/", "-").replace(" ", "_")
    attachments: list[tuple[str, str, bytes]] = []

    if fmt in ("pdf", "both"):
        try:
            loader = download_image_sync if attach_images else None
            pdf_bytes = generate_pdf(
                data,
                lang=lang,
                title=title,
                author=author,
                attach_images=attach_images,
                image_loader=loader,
            )
            attachments.append((f"invoices_{safe_period}.pdf", "application/pdf", pdf_bytes))
        except Exception as exc:
            return {"error": f"PDF generation failed: {exc}"}

    if fmt in ("excel", "both"):
        try:
            xlsx_bytes = generate_excel(data, lang=lang, title=title, author=author)
            attachments.append((
                f"invoices_{safe_period}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                xlsx_bytes,
            ))
        except Exception as exc:
            return {"error": f"Excel generation failed: {exc}"}

    if not attachments:
        return {"error": "No report files were generated."}

    try:
        await send_report_email(
            to_email=to_email,
            subject=subject,
            body=body,
            attachments=attachments,
        )
    except Exception as exc:
        logger.exception("Email send failed: %s", exc)
        return {"error": f"Email send failed: {exc}"}

    return {"ok": True, "sent_to": to_email, "invoice_count": len(data.rows)}
