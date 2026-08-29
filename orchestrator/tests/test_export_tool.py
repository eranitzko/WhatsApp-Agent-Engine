"""Tests for the orchestrator-level export_report tool."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import SessionCM


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

def test_invoice_generator_build_pdf_returns_bytes():
    from app.export.generators.invoice import InvoiceGenerator

    mock_row = MagicMock()
    mock_row.invoice_date = None
    mock_row.invoice_number = "INV-1"
    mock_row.vendor = "Acme"
    mock_row.description = "Widgets"
    mock_row.amount_original = 100
    mock_row.currency_original = "ILS"
    mock_row.amount_ils = 100
    mock_row.flagged = False

    mock_data = MagicMock()
    mock_data.rows = [mock_row]
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None
    mock_data.total_ils = 100
    mock_data.show_dual_currency = False

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice.render_pdf", return_value=b"pdf-bytes") as mock_render, \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        result = gen.build_pdf(month=5, year=2026)

    assert result == (b"pdf-bytes", "invoices_May_2026.pdf")

    # Verify the ReportSpec shape passed to render_pdf, not just the return value
    spec = mock_render.call_args.args[0]
    assert spec.title  # report_title_default label, non-empty
    section = spec.sections[0]
    assert [c.header for c in section.columns] == ["Date", "Invoice #", "Vendor", "Description", "Amount"]
    # currency_original="ILS" -> format_amount uses the symbol style, matching
    # the old _fmt_amount behavior (only non-ILS currencies get a code suffix)
    assert section.rows[0].cells == ["—", "INV-1", "Acme", "Widgets", "₪100.00"]
    assert section.totals_row.cells[-1] == "₪100.00"


def test_invoice_generator_build_pdf_flagged_row_gets_flagged_style():
    from app.export.generators.invoice import InvoiceGenerator

    mock_row = MagicMock()
    mock_row.invoice_date = None
    mock_row.invoice_number = "INV-1"
    mock_row.vendor = "Acme"
    mock_row.description = "Widgets"
    mock_row.amount_original = 100
    mock_row.currency_original = "ILS"
    mock_row.amount_ils = 100
    mock_row.flagged = True

    mock_data = MagicMock()
    mock_data.rows = [mock_row]
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None
    mock_data.total_ils = 100
    mock_data.show_dual_currency = False

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice.render_pdf", return_value=b"pdf-bytes") as mock_render, \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        gen.build_pdf(month=5, year=2026)

    spec = mock_render.call_args.args[0]
    assert spec.sections[0].rows[0].style == "flagged"
    # A flagged row's date cell gets the " *" suffix appended (caller's choice
    # of cell content, per the design doc — not a renderer concern)
    assert spec.sections[0].rows[0].cells[0] == "— *"
    # The flagged-note footer becomes an extra_flowable passed alongside the spec
    # (InvoiceGenerator.build_pdf always calls render_pdf(spec, extra_flowables=...) as a keyword arg)
    extra_flowables = mock_render.call_args.kwargs["extra_flowables"]
    assert extra_flowables is not None
    assert len(extra_flowables) >= 1


def test_invoice_generator_build_pdf_real_render_pdf_returns_bytes(db):
    """Un-mocked test: exercises InvoiceGenerator.build_pdf through the real
    render_pdf (not patched away), including a flagged row so the
    flagged-note extra_flowables path actually executes. Mirrors the pattern
    used by test_generate_ledger_pdf_returns_bytes — a real in-memory DB
    fixture, not a mocked-away DB layer."""
    from datetime import date
    from decimal import Decimal

    from app.db.models import GroupConfig
    from tests.conftest import make_invoice

    db.add(GroupConfig(group_id="123@g.us", feedback_language="en"))
    db.commit()
    make_invoice(
        db, group_id="123@g.us",
        invoice_date=date(2026, 5, 3), invoice_number="INV-1", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"), flagged=True,
    )

    from app.export.generators.invoice import InvoiceGenerator

    with patch("app.export.generators.invoice.SessionLocal", return_value=SessionCM(db)), \
         patch("app.reports.data.SessionLocal", return_value=SessionCM(db)):
        gen = InvoiceGenerator("123@g.us")
        result_bytes, filename = gen.build_pdf(month=5, year=2026)

    assert isinstance(result_bytes, bytes)
    assert len(result_bytes) > 100
    assert result_bytes[:4] == b"%PDF"
    assert filename == "invoices_May_2026.pdf"


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


def test_invoice_generator_build_pdf_language_override_ignores_group_config(db):
    """One-off language override: build_pdf(language="he") must render in
    Hebrew even though the group's saved feedback_language is "en" — and
    must NOT persist that change (still "en" after the call). Mirrors the
    one-off override family_accounting already had via fmt_config["language"];
    invoice_curator never had an equivalent, hence the bug."""
    from datetime import date
    from decimal import Decimal

    from app.db.models import GroupConfig
    from tests.conftest import make_invoice

    db.add(GroupConfig(group_id="123@g.us", feedback_language="en"))
    db.commit()
    make_invoice(
        db, group_id="123@g.us",
        invoice_date=date(2026, 5, 3), invoice_number="INV-1", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"),
    )

    from app.export.generators.invoice import InvoiceGenerator

    with patch("app.export.generators.invoice.SessionLocal", return_value=SessionCM(db)), \
         patch("app.reports.data.SessionLocal", return_value=SessionCM(db)):
        gen = InvoiceGenerator("123@g.us")
        result_bytes, _ = gen.build_pdf(month=5, year=2026, language="he")

    assert result_bytes[:4] == b"%PDF"
    # Not persisted — still "en" in the DB after a one-off override.
    cfg = db.get(GroupConfig, "123@g.us")
    assert cfg.feedback_language == "en"


def test_invoice_generator_build_pdf_language_override_sets_spec_lang():
    from app.export.generators.invoice import InvoiceGenerator

    mock_row = MagicMock()
    mock_row.invoice_date = None
    mock_row.invoice_number = "INV-1"
    mock_row.vendor = "Acme"
    mock_row.description = "Widgets"
    mock_row.amount_original = 100
    mock_row.currency_original = "ILS"
    mock_row.amount_ils = 100
    mock_row.flagged = False

    mock_data = MagicMock()
    mock_data.rows = [mock_row]
    mock_data.month = 5
    mock_data.year = 2026
    mock_data.period_label = None
    mock_data.total_ils = 100
    mock_data.show_dual_currency = False

    mock_cfg = MagicMock()
    mock_cfg.feedback_language = "en"
    mock_cfg.report_header = None
    mock_cfg.report_author = None
    mock_cfg.force_dual_currency = False

    with patch("app.export.generators.invoice.fetch_report_data", return_value=mock_data), \
         patch("app.export.generators.invoice.render_pdf", return_value=b"pdf-bytes") as mock_render, \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        gen.build_pdf(month=5, year=2026, language="he")

    spec = mock_render.call_args.args[0]
    assert spec.lang == "he"


def test_invoice_generator_build_xlsx_language_override_passed_to_generate_excel():
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
         patch("app.export.generators.invoice.generate_excel", return_value=b"xlsx") as mock_excel, \
         patch("app.export.generators.invoice._get_invoice_config", return_value=mock_cfg):
        gen = InvoiceGenerator("123@g.us")
        gen.build_xlsx(month=5, year=2026, language="he")

    assert mock_excel.call_args.kwargs["lang"] == "he"


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
    with patch("app.tools.accounting_export.SessionLocal", return_value=SessionCM(db)):
        result = generate_ledger_pdf("123@g.us")
    assert isinstance(result, bytes)
    assert len(result) > 100
    assert result[:4] == b"%PDF"


def test_generate_ledger_pdf_empty_group_returns_bytes(db):
    from app.tools.accounting_export import generate_ledger_pdf
    with patch("app.tools.accounting_export.SessionLocal", return_value=SessionCM(db)):
        result = generate_ledger_pdf("empty@g.us")
    assert isinstance(result, bytes)
    assert result[:4] == b"%PDF"


def test_generate_ledger_pdf_builds_correct_report_spec(db):
    from app.db.models import GroupParticipant, Blueprint, GroupRegistry, LedgerEntry
    from decimal import Decimal
    from datetime import date

    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="111", push_name="Alice"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="222", push_name="Bob"))
    db.add(LedgerEntry(
        transaction_id="tx1", group_jid="123@g.us", entry_type="debt",
        from_phone="111", to_phone="222",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        description="dinner", transaction_date=date(2026, 5, 1),
    ))
    db.commit()

    from app.tools import accounting_export

    captured_spec = {}

    def _capture_render_pdf(spec):
        captured_spec["spec"] = spec
        return b"%PDF-fake"

    with patch("app.tools.accounting_export.SessionLocal", return_value=SessionCM(db)), \
         patch("app.reports.render_pdf.render_pdf", side_effect=_capture_render_pdf):
        accounting_export.generate_ledger_pdf("123@g.us")

    spec = captured_spec["spec"]
    assert spec.sections[0].heading == "Net Balances"
    # Second section is the Alice/Bob pair table
    pair_section = spec.sections[1]
    assert "Alice" in pair_section.heading
    assert "Bob" in pair_section.heading
    assert pair_section.rows[0].cells[1] == "dinner"
    assert pair_section.totals_row.style == "total"


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

from app.db.models import Blueprint, GroupRegistry, ReportFormat


def _seed_bp_group(db, blueprint_id: str, group_jid: str = "123@g.us"):
    if not db.get(Blueprint, blueprint_id):
        db.add(Blueprint(id=blueprint_id, display_name=blueprint_id,
                         system_prompt="p", tools_enabled="[]"))
    if not db.query(GroupRegistry).filter_by(group_jid=group_jid).first():
        db.add(GroupRegistry(group_jid=group_jid, blueprint_id=blueprint_id))
    db.commit()


@pytest.mark.asyncio
async def test_export_report_invoice_pdf_to_group(db):
    _seed_bp_group(db, "invoice_curator")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "invoices_May_2026.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
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

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
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
async def test_export_invoice_report_non_admin_rejected(db):
    """export_invoice_report stays admin-only regardless of caller."""
    _seed_bp_group(db, "invoice_curator")
    from app.export.tool import _exec_export_report

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=False,
        )

    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_export_accounting_report_non_admin_allowed(db):
    """Any registered family_accounting member may export their own report —
    not just sys-admins. Regular household members had no way to get a PDF
    of their own ledger before this."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=False, sender="972500000102@s.whatsapp.net",
        )

    assert "admin" not in result.lower()
    mock_deliver.assert_called_once()


@pytest.mark.asyncio
async def test_export_accounting_report_non_admin_filters_to_own_phone(db):
    """A non-admin's report is scoped to their own ledger entries, not the
    whole household's — matching get_debt_summary's existing self-only
    behavior for non-admins."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report
    import app.export.tool as tool_mod

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch.object(tool_mod, "AccountingGenerator") as mock_cls, \
         patch("app.export.tool.deliver_files", AsyncMock()):
        mock_cls.return_value = mock_gen
        await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=False, sender="972500000102@s.whatsapp.net",
        )

    mock_cls.assert_called_once_with("123@g.us", filter_phone="972500000102")


@pytest.mark.asyncio
async def test_export_accounting_report_admin_sees_full_household(db):
    """An admin's report stays unfiltered — full household ledger."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report
    import app.export.tool as tool_mod

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch.object(tool_mod, "AccountingGenerator") as mock_cls, \
         patch("app.export.tool.deliver_files", AsyncMock()):
        mock_cls.return_value = mock_gen
        await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=True, sender="972500000101@s.whatsapp.net",
        )

    mock_cls.assert_called_once_with("123@g.us", filter_phone=None)


@pytest.mark.asyncio
async def test_export_report_unknown_blueprint_returns_error(db):
    _seed_bp_group(db, "notion_assistant")
    from app.export.tool import _exec_export_report

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)):
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


def test_export_invoice_report_schema_has_language_param():
    """Regression: export_invoice_report's schema never exposed a language
    override at all (unlike export_accounting_report's) — so the agent had
    no way to request one-off Hebrew, even though _exec_export_report always
    parsed a language param that only the family_accounting branch used."""
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    props = tools["export_invoice_report"]["schema"]["input_schema"]["properties"]
    assert "language" in props
    assert props["language"]["enum"] == ["en", "he"]


@pytest.mark.asyncio
async def test_export_report_invoice_pdf_language_override_passed_through(db):
    """The parsed language param must actually reach InvoiceGenerator for
    invoice_curator, not just for family_accounting."""
    _seed_bp_group(db, "invoice_curator")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "invoices_May_2026.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.InvoiceGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group", "language": "he"},
            group_jid="123@g.us", is_admin=True,
        )

    assert mock_gen.build_pdf.call_args.kwargs["language"] == "he"


@pytest.mark.asyncio
async def test_export_invoice_report_language_persists_as_new_default(db):
    """An explicit language on a report request is no longer just a one-off —
    it becomes the new default for future reports too, so an admin doesn't
    have to separately call update_config to make it stick. The generator
    itself stays side-effect-free (see
    test_invoice_generator_build_pdf_language_override_ignores_group_config);
    this persistence belongs at the orchestration layer instead."""
    from app.db.models import GroupConfig
    _seed_bp_group(db, "invoice_curator")
    db.add(GroupConfig(group_id="123@g.us", feedback_language="en"))
    db.commit()
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "invoices_May_2026.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.InvoiceGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", AsyncMock()):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group", "language": "he"},
            group_jid="123@g.us", is_admin=True,
        )

    cfg = db.query(GroupConfig).filter_by(group_id="123@g.us").first()
    assert cfg.feedback_language == "he"


@pytest.mark.asyncio
async def test_export_invoice_report_no_language_leaves_default_untouched(db):
    from app.db.models import GroupConfig
    _seed_bp_group(db, "invoice_curator")
    db.add(GroupConfig(group_id="123@g.us", feedback_language="en"))
    db.commit()
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "invoices_May_2026.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.InvoiceGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", AsyncMock()):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group"},
            group_jid="123@g.us", is_admin=True,
        )

    cfg = db.query(GroupConfig).filter_by(group_id="123@g.us").first()
    assert cfg.feedback_language == "en"


@pytest.mark.asyncio
async def test_export_accounting_report_language_persists_to_default_report_format(db):
    """Same policy for family_accounting — an explicit language on a report
    request becomes the new saved 'default' ReportFormat, without an
    admin needing to separately call create_report_format."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", AsyncMock()):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group", "language": "he"},
            group_jid="123@g.us", is_admin=True,
        )

    saved = db.query(ReportFormat).filter_by(group_jid="123@g.us", name="default").first()
    assert saved is not None
    assert saved.config()["language"] == "he"


@pytest.mark.asyncio
async def test_export_accounting_report_non_admin_language_also_persists(db):
    """is_admin is a GLOBAL flag (AdminNumbers table), not "owns this
    particular group" — a regular household member isn't in that table even
    in their own private group, but export_accounting_report already lets
    them manage their own ledger regardless. The persisted ReportFormat is
    keyed to the caller's OWN group_jid, never someone else's, so a non-admin
    setting their own report's language must also stick."""
    _seed_bp_group(db, "family_accounting")
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", AsyncMock()):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group", "language": "he"},
            group_jid="123@g.us", is_admin=False, sender="972500000102@s.whatsapp.net",
        )

    saved = db.query(ReportFormat).filter_by(group_jid="123@g.us", name="default").first()
    assert saved is not None
    assert saved.config()["language"] == "he"


@pytest.mark.asyncio
async def test_export_accounting_report_language_persist_preserves_other_saved_fields(db):
    """Persisting the new language must merge into an existing 'default'
    format, not clobber other fields an admin already customized via
    create_report_format (grouping, sections, etc.)."""
    import json
    _seed_bp_group(db, "family_accounting")
    db.add(ReportFormat(group_jid="123@g.us", name="default", config_json=json.dumps({
        "language": "en", "grouping": "by_person", "sections": ["balances"],
    })))
    db.commit()
    from app.export.tool import _exec_export_report

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", AsyncMock()):
        await _exec_export_report(
            {"format": "pdf", "delivery": "group", "language": "he"},
            group_jid="123@g.us", is_admin=True,
        )

    saved = db.query(ReportFormat).filter_by(group_jid="123@g.us", name="default").first()
    config = saved.config()
    assert config["language"] == "he"
    assert config["grouping"] == "by_person"
    assert config["sections"] == ["balances"]


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

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
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


@pytest.mark.asyncio
async def test_export_report_uses_resolved_phone_not_raw_lid(db):
    """Regression: sender_phone for the UserProfile email lookup must come
    from resolved_phone, not a raw LID split — a mismatch silently falls
    back to the default report email instead of the user's own."""
    from app.db.models import UserProfile
    from app.export.tool import _exec_export_report

    _seed_bp_group(db, "family_accounting", group_jid="456@g.us")
    db.add(UserProfile(phone="972523206175", email="eran@example.com"))
    db.commit()

    mock_gen = MagicMock()
    mock_gen.build_pdf.return_value = (b"pdf", "ledger.pdf")
    mock_deliver = AsyncMock()

    with patch("app.export.tool.SessionLocal", return_value=SessionCM(db)), \
         patch("app.export.tool.AccountingGenerator", return_value=mock_gen), \
         patch("app.export.tool.deliver_files", mock_deliver):
        result = await _exec_export_report(
            {"format": "pdf", "delivery": "email"},
            is_admin=True, group_jid="456@g.us",
            sender="175715853041683@lid", resolved_phone="972523206175",
        )

    # Before the fix: sender_phone computed from the raw LID never matches
    # UserProfile.phone, so _resolve_email falls through to the default (None
    # in this test's settings) and delivery is rejected with "No email
    # address available" — the observable proxy that the wrong phone was used.
    assert "No email address available" not in result
    mock_deliver.assert_called_once()
    assert mock_deliver.call_args.kwargs["email"] == "eran@example.com"
