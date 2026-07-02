import io

import pdfplumber
import pytest

from app.reports.spec import Column, ReportSpec, Row, Run, TableSection
from app.reports.render_pdf import render_pdf


def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_render_pdf_english_table_returns_valid_pdf_with_content():
    spec = ReportSpec(
        title="Test Report",
        lang="en",
        generated_label="Generated: 2026-07-01",
        sections=[
            TableSection(
                heading="Items",
                columns=[
                    Column(header="Date"),
                    Column(header="Description"),
                    Column(header="Amount", type="number"),
                ],
                rows=[Row(cells=["01/07/2026", "Dinner", "100.00 ILS"])],
            )
        ],
    )
    pdf_bytes = render_pdf(spec)

    assert pdf_bytes[:4] == b"%PDF"
    text = _extract_text(pdf_bytes)
    assert "Test Report" in text
    assert "Items" in text
    assert "Dinner" in text
    assert "100.00 ILS" in text


def test_render_pdf_hebrew_table_with_mixed_english_content():
    """A Hebrew-language report can contain English free text (e.g. a Netflix
    subscription line item) — it must render in correct left-to-right order,
    not reversed, since bidi is a property of the content, not the report."""
    spec = ReportSpec(
        title="דוח בדיקה",
        lang="he",
        generated_label="נוצר: 01/07/2026",
        sections=[
            TableSection(
                heading="פריטים",
                columns=[
                    Column(header="תאריך"),
                    Column(header="תיאור"),
                    Column(header="סכום", type="number"),
                ],
                rows=[Row(cells=["01/07/2026", "Netflix subscription", "-50.00 ILS"])],
            )
        ],
    )
    pdf_bytes = render_pdf(spec)
    assert pdf_bytes[:4] == b"%PDF"
    text = _extract_text(pdf_bytes)
    assert "Netflix subscription" in text
    assert "-50.00 ILS" in text


def test_render_pdf_total_row_is_styled_and_readable():
    spec = ReportSpec(
        title="Test", lang="en", generated_label="Generated: today",
        sections=[TableSection(
            columns=[Column(header="Name"), Column(header="Amount", type="number")],
            rows=[Row(cells=["Eran", "100.00 ILS"])],
            totals_row=Row(cells=["Total", "100.00 ILS"], style="total"),
        )],
    )
    pdf_bytes = render_pdf(spec)
    text = _extract_text(pdf_bytes)
    assert "Total" in text
    assert "Eran" in text


def test_render_pdf_flagged_row_and_run_level_bold():
    spec = ReportSpec(
        title="Test", lang="en", generated_label="Generated: today",
        sections=[TableSection(
            columns=[Column(header="Date"), Column(header="Note")],
            rows=[
                Row(
                    cells=["01/07/2026", [Run(text="Important", bold=True), Run(text=" — check this")]],
                    style="flagged",
                ),
            ],
        )],
    )
    pdf_bytes = render_pdf(spec)
    assert pdf_bytes[:4] == b"%PDF"
    text = _extract_text(pdf_bytes)
    assert "Important" in text
    assert "check this" in text


def test_render_pdf_empty_section_shows_message_not_empty_table():
    spec = ReportSpec(
        title="Test", lang="en", generated_label="Generated: today",
        sections=[TableSection(
            columns=[Column(header="Name"), Column(header="Amount", type="number")],
            rows=[],
            empty_message="No transactions found.",
        )],
    )
    pdf_bytes = render_pdf(spec)
    text = _extract_text(pdf_bytes)
    assert "No transactions found." in text


def test_render_pdf_raises_on_row_column_count_mismatch():
    spec = ReportSpec(
        title="Test", lang="en", generated_label="Generated: today",
        sections=[TableSection(
            columns=[Column(header="A"), Column(header="B")],
            rows=[Row(cells=["only one"])],
        )],
    )
    with pytest.raises(ValueError):
        render_pdf(spec)


def test_render_pdf_raises_on_totals_row_column_count_mismatch():
    spec = ReportSpec(
        title="Test", lang="en", generated_label="Generated: today",
        sections=[TableSection(
            columns=[Column(header="A"), Column(header="B")],
            rows=[Row(cells=["x", "y"])],
            totals_row=Row(cells=["only one"], style="total"),
        )],
    )
    with pytest.raises(ValueError):
        render_pdf(spec)
