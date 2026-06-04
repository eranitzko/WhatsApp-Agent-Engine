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
