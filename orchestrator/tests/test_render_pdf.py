import io

import pdfplumber
import pytest

from app.reports.spec import Column, ReportSpec, Row, Run, TableSection
from app.reports.render_pdf import render_pdf


def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# Rounded (3 dp) RGB fractions for render_pdf.py's _TOTAL_BG (#e8f0fe) and
# _FLAG_BG (#fff3cd), as reportlab.lib.colors.HexColor exposes them and as
# pdfplumber reports a rect's non_stroking_color. Verified empirically against
# the real generated PDF, not derived by hand from the hex codes.
_TOTAL_BG_RGB = (0.910, 0.941, 0.996)
_FLAG_BG_RGB = (1.0, 0.953, 0.804)


def _has_rect_with_fill(page, rgb: tuple[float, float, float], tol: float = 0.005) -> bool:
    """True if any filled rect on the page has a non_stroking_color matching
    rgb within tol. Searches all rects rather than assuming a position/order,
    since row-style background overrides are drawn as extra rects layered on
    top of the base alternating-row backgrounds."""
    for r in page.rects:
        color = r.get("non_stroking_color")
        if not color or len(color) != 3:
            continue
        if all(abs(c - t) <= tol for c, t in zip(color, rgb)):
            return True
    return False


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

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        assert _has_rect_with_fill(page, _TOTAL_BG_RGB), (
            "expected a rect filled with the total-row background color "
            f"{_TOTAL_BG_RGB}, found fills: "
            f"{[r.get('non_stroking_color') for r in page.rects]}"
        )


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

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        assert _has_rect_with_fill(page, _FLAG_BG_RGB), (
            "expected a rect filled with the flagged-row background color "
            f"{_FLAG_BG_RGB}, found fills: "
            f"{[r.get('non_stroking_color') for r in page.rects]}"
        )

        # Locate the "Important" and "check this" substrings within the page's
        # character stream (in reading order) so we can inspect their fontname
        # directly — pdfplumber doesn't expose bold as a boolean, but the
        # registered bold font's name contains "Bold" (see _FONT_BOLD /
        # Helvetica-Bold in render_pdf.py's _font()).
        full_text = "".join(c["text"] for c in page.chars)
        idx_important = full_text.find("Important")
        idx_check = full_text.find("check this")
        assert idx_important != -1
        assert idx_check != -1

        important_chars = page.chars[idx_important: idx_important + len("Important")]
        check_chars = page.chars[idx_check: idx_check + len("check this")]

        important_fonts = {c["fontname"] for c in important_chars}
        check_fonts = {c["fontname"] for c in check_chars}

        assert important_fonts and all("Bold" in f for f in important_fonts), (
            f"expected 'Important' to render in a bold font, got: {important_fonts}"
        )
        assert check_fonts and all("Bold" not in f for f in check_fonts), (
            f"expected 'check this' to render in a non-bold font, got: {check_fonts}"
        )


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
