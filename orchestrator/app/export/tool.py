"""export_report — orchestrator-level report tool for all blueprints."""

from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.export.delivery import deliver_files
from app.export.generators.accounting import AccountingGenerator
from app.export.generators.invoice import InvoiceGenerator, NoDataError

logger = logging.getLogger(__name__)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _resolve_email(params: dict, sender_phone: str) -> str | None:
    email = params.get("email", "").strip()
    if email:
        return email
    with SessionLocal() as db:
        from app.db.models import UserProfile
        profile = db.get(UserProfile, sender_phone)
        if profile and profile.email:
            return profile.email
    from app.config import settings
    return settings.default_report_email or settings.gmail_user or None


def _blueprint_for_group(group_jid: str, db) -> str | None:
    from app.db.models import GroupRegistry
    row = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
    return row.blueprint_id if row else None


async def _exec_export_report(params: dict, **ctx) -> str:
    if not ctx.get("is_admin", False):
        return "Export is admin only."

    group_jid: str = ctx.get("group_jid", "")
    sender_phone: str = (ctx.get("sender", "")).split("@")[0].split(":")[0]

    fmt = params.get("format", "pdf")
    delivery = params.get("delivery", "group")
    month = params.get("month")
    year = params.get("year")
    attach_images = bool(params.get("attach_images", False))
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    custom_subject = params.get("subject", "").strip()
    custom_body = params.get("body", "").strip()

    email = _resolve_email(params, sender_phone) if delivery in ("email", "both") else None
    if delivery in ("email", "both") and not email:
        return "No email address available. Provide one or save it with save_email."

    with SessionLocal() as db:
        blueprint_id = _blueprint_for_group(group_jid, db)

    if blueprint_id is None:
        return "Group not registered."

    files: list[tuple[str, str, bytes]] = []

    try:
        if blueprint_id == "invoice_curator":
            gen = InvoiceGenerator(group_jid)
            if fmt in ("pdf", "both"):
                data, name = gen.build_pdf(month=month, year=year,
                                           attach_images=attach_images,
                                           start_date=start_date, end_date=end_date)
                files.append((name, "application/pdf", data))
            if fmt in ("xlsx", "both"):
                data, name = gen.build_xlsx(month=month, year=year,
                                            start_date=start_date, end_date=end_date)
                files.append((name, _XLSX_MIME, data))

        elif blueprint_id == "family_accounting":
            gen = AccountingGenerator(group_jid)
            if fmt in ("pdf", "both"):
                data, name = gen.build_pdf()
                files.append((name, "application/pdf", data))
            if fmt in ("xlsx", "both"):
                data, name = gen.build_xlsx()
                files.append((name, _XLSX_MIME, data))

        else:
            return f"Export not supported for blueprint '{blueprint_id}'."

    except NoDataError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception("export_report: generation failed for %s", group_jid)
        return f"Report generation failed: {exc}"

    if not files:
        return "No files generated."

    period_hint = f"{month}/{year}" if month and year else "current period"

    if custom_subject or custom_body:
        from app.automation.context import WorkflowContext
        with SessionLocal() as db:
            wf_ctx = WorkflowContext(group_jid, db=db)
            subject = wf_ctx.resolve(custom_subject) if custom_subject else f"Report — {group_jid.split('@')[0]} — {period_hint}"
            body = wf_ctx.resolve(custom_body) if custom_body else f"Please find the {fmt.upper()} report attached."
    else:
        subject = f"Report — {group_jid.split('@')[0]} — {period_hint}"
        body = f"Please find the {fmt.upper()} report attached."

    try:
        await deliver_files(
            group_jid=group_jid,
            email=email,
            delivery=delivery,
            files=files,
            subject=subject,
            body=body,
        )
    except Exception as exc:
        logger.exception("export_report: delivery failed for %s", group_jid)
        return f"Report generated but delivery failed: {exc}"

    destinations = []
    if delivery in ("group", "both"):
        destinations.append("the group")
    if delivery in ("email", "both") and email:
        destinations.append(email)
    names = ", ".join(f[0] for f in files)
    return f"Sent {names} to {' and '.join(destinations)}."


_SCHEMA = {
    "name": "export_report",
    "category": "export",
    "description": (
        "Generate a PDF and/or XLSX report for this group and deliver it to the group chat "
        "and/or by email. Admin only. For invoice groups, PDF can include invoice images. "
        "For accounting groups, PDF shows net balances and full transaction history."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["pdf", "xlsx", "both"],
                "description": "Report format. Default: pdf.",
            },
            "delivery": {
                "type": "string",
                "enum": ["group", "email", "both"],
                "description": "Where to send the report. Default: group.",
            },
            "email": {
                "type": "string",
                "description": "Email address to send to. Optional — uses saved email or DEFAULT_REPORT_EMAIL if omitted.",
            },
            "month": {
                "type": "integer",
                "description": "Month 1-12. Defaults to current month. Invoice curator only.",
            },
            "year": {
                "type": "integer",
                "description": "4-digit year. Defaults to current year. Invoice curator only.",
            },
            "attach_images": {
                "type": "boolean",
                "description": "Include invoice images in PDF appendix. Invoice curator only. Default: false.",
            },
            "start_date": {
                "type": "string",
                "description": "Custom range start YYYY-MM-DD. Invoice curator only.",
            },
            "end_date": {
                "type": "string",
                "description": "Custom range end YYYY-MM-DD. Invoice curator only.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject. Supports {{variables}} (e.g. {{previous_month}}). Used only when delivery includes email.",
            },
            "body": {
                "type": "string",
                "description": "Email body text. Supports {{variables}} (e.g. {{monthly_invoice_total}}). Used only when delivery includes email.",
            },
        },
        "required": [],
    },
}


def get_export_tools() -> dict[str, dict]:
    return {
        "export_report": {
            "schema": _SCHEMA,
            "executor": _exec_export_report,
        }
    }
