import io

import pdfplumber

from app.reports.spec import Column, ReportSpec, Row, TableSection
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
