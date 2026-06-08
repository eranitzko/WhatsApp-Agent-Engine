"""Tests for the orchestrator-level export_report tool."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── delivery tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_files_to_group_calls_send_file():
    from app.export.delivery import deliver_files

    mock_send = AsyncMock()
    with patch("app.export.delivery.send_file", mock_send):
        await deliver_files(
            group_jid="123@g.us",
            email=None,
            delivery="group",
            files=[("report.pdf", "application/pdf", b"pdf-data")],
        )

    mock_send.assert_called_once_with("123@g.us", "report.pdf", "application/pdf", b"pdf-data")


@pytest.mark.asyncio
async def test_deliver_files_by_email_calls_send_report_email():
    from app.export.delivery import deliver_files

    mock_mail = MagicMock()

    async def fake_to_thread(fn, **kwargs):
        fn(**kwargs)

    with patch("app.export.delivery.send_report_email", mock_mail), \
         patch("app.export.delivery.asyncio.to_thread", fake_to_thread):
        await deliver_files(
            group_jid="123@g.us",
            email="user@example.com",
            delivery="email",
            files=[("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"xlsx")],
            subject="My Report",
            body="See attached.",
        )

    mock_mail.assert_called_once_with(
        to="user@example.com",
        subject="My Report",
        body="See attached.",
        attachments=[("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"xlsx")],
    )


@pytest.mark.asyncio
async def test_deliver_files_both_calls_send_file_and_email():
    from app.export.delivery import deliver_files

    mock_send = AsyncMock()
    mock_mail = MagicMock()

    async def fake_to_thread(fn, **kwargs):
        fn(**kwargs)

    with patch("app.export.delivery.send_file", mock_send), \
         patch("app.export.delivery.send_report_email", mock_mail), \
         patch("app.export.delivery.asyncio.to_thread", fake_to_thread):
        await deliver_files(
            group_jid="123@g.us",
            email="user@example.com",
            delivery="both",
            files=[("report.pdf", "application/pdf", b"pdf")],
            subject="Report",
            body="Attached.",
        )

    mock_send.assert_called_once()
    mock_mail.assert_called_once()


# ── invoice generator tests ───────────────────────────────────────────────────

def test_invoice_generator_generate_pdf_returns_bytes():
    from app.export.generators.invoice import InvoiceGenerator

    mock_data = MagicMock()
    mock_data.rows = [MagicMock()]
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None
    mock_data.total_ils = 100

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice.generate_pdf", return_value=b"pdf-bytes"), \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        result = gen.build_pdf(month=5, year=2026)

    assert result == (b"pdf-bytes", "invoices_May_2026.pdf")


def test_invoice_generator_generate_xlsx_returns_bytes():
    from app.export.generators.invoice import InvoiceGenerator

    mock_data = MagicMock()
    mock_data.rows = [MagicMock()]
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice.generate_excel", return_value=b"xlsx-bytes"), \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        result = gen.build_xlsx(month=5, year=2026)

    assert result == (b"xlsx-bytes", "invoices_May_2026.xlsx")


def test_invoice_generator_no_data_raises():
    from app.export.generators.invoice import InvoiceGenerator, NoDataError

    mock_data = MagicMock()
    mock_data.rows = []
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        with pytest.raises(NoDataError):
            gen.build_pdf(month=5, year=2026)


# ── ledger PDF tests ──────────────────────────────────────────────────────────

class _CM:
    """Wrap a plain SQLAlchemy Session as a context manager for patching SessionLocal."""
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        pass


def test_generate_ledger_pdf_returns_bytes(db):
    from app.db.models import GroupParticipant, Blueprint, GroupRegistry, LedgerEntry
    from decimal import Decimal
    from datetime import date

    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="111", push_name="Alice"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="222", push_name="Bob"))
    db.add(LedgerEntry(
        transaction_id="tx1", group_jid="123@g.us",
        from_phone="111", to_phone="222",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        description="dinner", transaction_date=date(2026, 5, 1),
    ))
    db.commit()

    from app.tools.accounting_export import generate_ledger_pdf
    with patch("app.tools.accounting_export.SessionLocal", return_value=_CM(db)):
        result = generate_ledger_pdf("123@g.us")
    assert isinstance(result, bytes)
    assert len(result) > 100
    assert result[:4] == b"%PDF"


def test_generate_ledger_pdf_empty_group_returns_bytes(db):
    from app.tools.accounting_export import generate_ledger_pdf
    with patch("app.tools.accounting_export.SessionLocal", return_value=_CM(db)):
        result = generate_ledger_pdf("empty@g.us")
    assert isinstance(result, bytes)
    assert result[:4] == b"%PDF"


# ── accounting generator tests ────────────────────────────────────────────────

def test_accounting_generator_build_xlsx_returns_bytes():
    from app.export.generators.accounting import AccountingGenerator

    with patch("app.export.generators.accounting.generate_ledger_xlsx", return_value=b"xlsx"):
        gen = AccountingGenerator("123@g.us")
        result = gen.build_xlsx()

    assert result == (b"xlsx", "ledger.xlsx")


def test_accounting_generator_build_pdf_returns_bytes():
    from app.export.generators.accounting import AccountingGenerator

    with patch("app.export.generators.accounting.generate_ledger_pdf", return_value=b"%PDF"):
        gen = AccountingGenerator("123@g.us")
        result = gen.build_pdf()

    assert result == (b"%PDF", "ledger.pdf")


# ── export_report tool tests ──────────────────────────────────────────────────

from app.db.models import Blueprint, GroupRegistry


def _seed_bp_group(db, blueprint_id: str, group_jid: str = "123@g.us"):
    if not db.get(Blueprint, blueprint_id):
        db.add(Blueprint(id=blueprint_id, display_name=blueprint_id,
                         system_prompt="p", tools_enabled="[]"))
    if not db.query(GroupRegistry).filter_by(group_jid=group_jid).first():
        db.add(GroupRegistry(group_jid=group_jid, blueprint_id=blueprint_id))
    db.commit()


class _CM2:
    def __init__(self, s): self._s = s
    def __enter__(self): return self._s
    def __exit__(self, *a): pass


@pytest.mark.asyncio
async def test_export_report_invoice_pdf_to_group(db):
    _seed_bp_group(db, "invoice_curator")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "invoices_May_2026.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=_CM2(db)), \
         patch("app.export.tool.InvoiceGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=True,
        )

    assert "sent" in result.lower() or "ok" in result.lower()
    mock_deliver.assert_called_once()
    call_kwargs = mock_deliver.call_args.kwargs
    assert call_kwargs["delivery"] == "group"
    assert call_kwargs["files"][0][0] == "invoices_May_2026.pdf"


@pytest.mark.asyncio
async def test_export_report_accounting_xlsx_by_email(db):
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report
    from app.config import settings

    mock_gen = MagicMock()
    mock_gen.build_xlsx.return_value = (b"xlsx", "ledger.xlsx")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=_CM2(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver), \
         patch.object(settings, "default_report_email", "admin@example.com"):
        result = await _exec_export_report(
            {"format": "xlsx", "delivery": "email"},
            group_jid="123@g.us", is_admin=True,
        )

    mock_deliver.assert_called_once()
    call_kwargs = mock_deliver.call_args.kwargs
    assert call_kwargs["delivery"] == "email"
    assert call_kwargs["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_export_report_non_admin_rejected(db):
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report

    with patch("app.export.tool.SessionLocal", return_value=_CM2(db)):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=False,
        )

    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_export_report_unknown_blueprint_returns_error(db):
    _seed_bp_group(db, "notion_assistant")
    from app.export.tool import _exec_export_report

    with patch("app.export.tool.SessionLocal", return_value=_CM2(db)):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=True,
        )

    assert "not supported" in result.lower()


def test_export_invoice_report_tool_exists():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    assert "export_invoice_report" in tools
    assert "export_accounting_report" in tools
    assert "export_report" not in tools


def test_export_invoice_report_has_invoice_params():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    props = tools["export_invoice_report"]["schema"]["input_schema"]["properties"]
    assert "month" in props
    assert "attach_images" in props


def test_export_accounting_report_has_no_invoice_params():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    props = tools["export_accounting_report"]["schema"]["input_schema"]["properties"]
    assert "month" not in props
    assert "attach_images" not in props
    assert "start_date" not in props
    assert "year" not in props


@pytest.mark.asyncio
async def test_export_report_custom_subject_body_resolved_via_workflow_context(db):
    """subject/body params with {{variables}} are resolved before delivery — not sent raw."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report
    from app.config import settings

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"%PDF", "ledger.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=_CM2(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver), \
         patch.object(settings, "default_report_email", "admin@example.com"), \
         patch("app.automation.context.WorkflowContext") as mock_wf_cls:

        mock_wf = MagicMock()
        mock_wf.resolve.side_effect = lambda t: t.replace("{{previous_month}}", "May 2026")
        mock_wf_cls.return_value = mock_wf

        await _exec_export_report(
            {"format": "pdf", "delivery": "email",
             "subject": "Report for {{previous_month}}",
             "body": "Hi, here is your {{previous_month}} report."},
            group_jid="123@g.us", is_admin=True,
        )

    call_kwargs = mock_deliver.call_args.kwargs
    assert call_kwargs["subject"] == "Report for May 2026"
    assert call_kwargs["body"] == "Hi, here is your May 2026 report."
