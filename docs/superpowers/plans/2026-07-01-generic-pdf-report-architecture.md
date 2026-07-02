# Generic PDF Report Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two independent, hand-rolled PDF generators (`pdf_report.py` for invoice_curator, `accounting_export.py`'s `generate_ledger_pdf` for family_accounting) with one generic `ReportSpec` → `render_pdf()` contract that any current or future blueprint uses.

**Architecture:** New `app/reports/spec.py` (dataclasses), `app/reports/formatting.py` (shared date/currency formatting), `app/reports/render_pdf.py` (the single renderer — owns Unicode font registration, bidi, RTL layout). Blueprint code (`InvoiceGenerator`, `AccountingGenerator`) builds a `ReportSpec` from its own DB query and calls `render_pdf(spec)`. Old bespoke drawing code is deleted once each blueprint is migrated and verified — no dead code, no permanent fallback.

**Tech Stack:** Python 3.12, ReportLab (PDF), `python-bidi` (RTL), `pdfplumber` (new — PDF text extraction for test verification), pytest.

**Design doc:** `docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md` — read this first if anything below is unclear on *why*.

---

## File map

| File | What changes |
|------|-------------|
| `orchestrator/requirements.txt` | Add `pdfplumber` (test-only, for PDF text-order assertions) |
| `orchestrator/app/reports/spec.py` | New — `Run`, `Column`, `Row`, `TableSection`, `ReportSpec` dataclasses |
| `orchestrator/app/reports/formatting.py` | New — `format_date`, `format_currency` (moved from `accounting_export.py`) |
| `orchestrator/app/reports/render_pdf.py` | New — the single renderer: font/bidi/RTL + section/table/row/cell drawing |
| `orchestrator/app/reports/pdf_report.py` | Stripped down to only `_build_appendix`/`_load_rl_image` (imports font/bidi from `render_pdf.py`); `generate_pdf`, `_build_table`, `_register_font`, `_font`, `_bidi`, `_xml` all deleted |
| `orchestrator/app/export/generators/invoice.py` | `build_pdf` rewritten to build a `ReportSpec` and call `render_pdf` |
| `orchestrator/app/tools/accounting_export.py` | `generate_ledger_pdf` rewritten to build a `ReportSpec` and call `render_pdf`; local `_fmt_date`/`_fmt_currency` deleted, replaced by imports from `formatting.py` (also used by `generate_ledger_xlsx`, unchanged behavior) |
| `orchestrator/tests/test_formatting.py` | New |
| `orchestrator/tests/test_render_pdf.py` | New |
| `orchestrator/tests/test_export_tool.py` | `InvoiceGenerator`/`AccountingGenerator` PDF tests updated to assert on `ReportSpec` shape, not just byte length |

---

## Task 1: `ReportSpec` dataclasses

**Files:**
- Create: `orchestrator/app/reports/spec.py`
- Test: `orchestrator/tests/test_reports_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/tests/test_reports_spec.py
from app.reports.spec import Column, ReportSpec, Row, Run, TableSection


def test_row_defaults_to_normal_style():
    row = Row(cells=["a", "b"])
    assert row.style == "normal"


def test_column_defaults_to_text_type_and_weight_one():
    col = Column(header="Name")
    assert col.type == "text"
    assert col.width_weight == 1.0


def test_report_spec_builds_nested_structure():
    spec = ReportSpec(
        title="Test Report",
        lang="en",
        generated_label="Generated: 2026-07-01",
        sections=[
            TableSection(
                heading="Section A",
                columns=[Column(header="Date"), Column(header="Amount", type="number")],
                rows=[Row(cells=["01/07/2026", "100.00 ILS"])],
                totals_row=Row(cells=["Total", "100.00 ILS"], style="total"),
            )
        ],
    )
    assert spec.meta_lines == []
    assert spec.sections[0].heading == "Section A"
    assert spec.sections[0].totals_row.style == "total"


def test_cell_accepts_plain_string_or_run_list():
    row_plain = Row(cells=["hello"])
    row_runs = Row(cells=[[Run(text="bold part", bold=True), Run(text=" plain part")]])
    assert row_plain.cells[0] == "hello"
    assert row_runs.cells[0][0].bold is True
    assert row_runs.cells[0][1].bold is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_reports_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.reports.spec'` (or similar import error)

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/app/reports/spec.py
"""Generic report specification — the data blueprint code builds and hands to
render_pdf().

Every blueprint (invoice_curator, family_accounting, and any future one)
describes its report as a ReportSpec: an ordered list of TableSections, each
with typed columns and already-formatted string cells. render_pdf()
(app/reports/render_pdf.py) knows nothing about invoices or ledgers — only
how to lay out sections, tables, rows, and cells. See
docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ColumnType = Literal["text", "number"]
RowStyle = Literal["normal", "total", "flagged"]


@dataclass
class Run:
    """A styled span of text within a cell, for character/word-level styling."""
    text: str
    bold: bool = False
    color: str | None = None   # hex string e.g. "#1a3c5e"; None = default


Cell = str | list[Run]


@dataclass
class Column:
    header: str
    type: ColumnType = "text"
    width_weight: float = 1.0


@dataclass
class Row:
    cells: list[Cell]
    style: RowStyle = "normal"


@dataclass
class TableSection:
    columns: list[Column]
    rows: list[Row]
    heading: str | None = None
    totals_row: Row | None = None
    empty_message: str | None = None   # shown instead of an empty table when rows == []


@dataclass
class ReportSpec:
    title: str
    lang: Literal["en", "he"]
    generated_label: str
    sections: list[TableSection]
    meta_lines: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_reports_spec.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/reports/spec.py orchestrator/tests/test_reports_spec.py
git commit -m "feat: add ReportSpec generic report contract dataclasses"
```

---

## Task 2: Shared formatting helpers

**Files:**
- Create: `orchestrator/app/reports/formatting.py`
- Test: `orchestrator/tests/test_formatting.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/tests/test_formatting.py
from datetime import date

from app.reports.formatting import format_amount, format_currency, format_date


def test_format_date_dd_mm_yyyy():
    assert format_date(date(2026, 7, 1), "DD/MM/YYYY") == "01/07/2026"


def test_format_date_yyyy_mm_dd():
    assert format_date(date(2026, 7, 1), "YYYY-MM-DD") == "2026-07-01"


def test_format_date_dd_mmm_yyyy():
    assert format_date(date(2026, 7, 1), "DD MMM YYYY") == "01 Jul 2026"


def test_format_date_none_returns_empty_string():
    assert format_date(None, "DD/MM/YYYY") == ""


def test_format_date_unknown_format_falls_back_to_iso():
    assert format_date(date(2026, 7, 1), "nonsense") == "2026-07-01"


def test_format_currency_positive_ils_symbol():
    assert format_currency(100.0, "₪") == "₪100.00"


def test_format_currency_positive_ils_suffix():
    assert format_currency(100.0, "ILS") == "100.00 ILS"


def test_format_currency_negative_sign_before_symbol():
    assert format_currency(-50.0, "₪") == "-₪50.00"


def test_format_currency_negative_sign_before_suffix():
    assert format_currency(-50.0, "ILS") == "-50.00 ILS"


def test_format_currency_thousands_separator():
    assert format_currency(1234.5, "₪") == "₪1,234.50"


def test_format_amount_ils_uses_symbol():
    assert format_amount(1234.5, "ILS") == "₪1,234.50"


def test_format_amount_foreign_currency_uses_code_suffix():
    assert format_amount(99.9, "USD") == "99.90 USD"


def test_format_amount_none_amount_returns_dash():
    assert format_amount(None, "USD") == "—"


def test_format_amount_none_currency_omits_suffix():
    assert format_amount(50.0, None) == "50.00 "
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_formatting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.reports.formatting'`

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/app/reports/formatting.py
"""Shared value-formatting helpers for report generation.

Blueprint code calls these to turn raw Decimal/date values into final display
strings *before* building Row/Cell objects — render_pdf never formats a raw
number or date itself. See
docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md.
"""
from __future__ import annotations

from datetime import date as date_type

_DATE_FORMATS = {
    "DD/MM/YYYY": lambda d: d.strftime("%d/%m/%Y") if d else "",
    "YYYY-MM-DD": lambda d: d.isoformat() if d else "",
    "DD MMM YYYY": lambda d: d.strftime("%d %b %Y") if d else "",
}


def format_date(d: date_type | None, date_format: str) -> str:
    formatter = _DATE_FORMATS.get(date_format, _DATE_FORMATS["YYYY-MM-DD"])
    return formatter(d)


def format_currency(amount: float, currency_display: str) -> str:
    """Sign-aware ILS-only formatter: currency_display picks symbol ("₪") vs
    suffix ("ILS") display style for an amount that is always ILS. Used by
    family_accounting, where amounts can be negative (payments)."""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if currency_display == "₪":
        return f"{sign}₪{amount:,.2f}"
    return f"{sign}{amount:,.2f} ILS"


def format_amount(amount: float | None, currency: str | None) -> str:
    """Multi-currency formatter: ILS gets the ₪ symbol, any other currency
    gets its code as a suffix. Used by invoice_curator, where an invoice's
    original amount can be in any currency and is never negative."""
    if amount is None:
        return "—"
    if currency == "ILS":
        return f"₪{amount:,.2f}"
    return f"{amount:,.2f} {currency or ''}"
```

Note both functions now use `:,.2f` (thousands separator) — the pre-migration `_fmt_currency` in `accounting_export.py` used plain `.2f` while `pdf_report.py`'s `_fmt_amount`/`_fmt_ils` used `:,.2f`, an inconsistency between the two systems that consolidating into one shared module surfaces and fixes (same category of fix as the bidi/escape-order bug — a natural byproduct of having one implementation instead of two that quietly disagreed).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_formatting.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/reports/formatting.py orchestrator/tests/test_formatting.py
git commit -m "feat: add shared format_date/format_currency/format_amount helpers"
```

---

## Task 3: `render_pdf` — font/bidi foundation + minimal English table

**Files:**
- Add dependency: `orchestrator/requirements.txt`
- Create: `orchestrator/app/reports/render_pdf.py`
- Test: `orchestrator/tests/test_render_pdf.py`

- [ ] **Step 1: Add pdfplumber to requirements.txt**

Add this line to `orchestrator/requirements.txt` (anywhere in the file, alphabetical position near existing `p`-prefixed packages if the file is sorted, otherwise append):

```
pdfplumber==0.11.9
```

Run: `pip install pdfplumber==0.11.9`
Expected: installs cleanly (already present in dev environment per earlier verification — this just makes it an explicit, reproducible dependency)

- [ ] **Step 2: Write the failing test**

```python
# orchestrator/tests/test_render_pdf.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_render_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.reports.render_pdf'`

- [ ] **Step 4: Write the implementation**

```python
# orchestrator/app/reports/render_pdf.py
"""Generic PDF renderer — the single canonical implementation of Unicode/RTL/
bidi table rendering for any blueprint's report.

Knows nothing about invoices, ledgers, or any other business domain — it only
knows how to draw a title, meta lines, and TableSections built from a
ReportSpec. Every cell arrives already formatted as a display string (see
app/reports/formatting.py); this module never inspects a raw number or date.
See docs/superpowers/specs/2026-07-01-generic-pdf-report-architecture-design.md.
"""
from __future__ import annotations

import io
import logging
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.reports.spec import Cell, Column, ReportSpec, Row, Run, TableSection

logger = logging.getLogger(__name__)

# ── bidi ──────────────────────────────────────────────────────────────────────

try:
    from bidi.algorithm import get_display as _get_display
except ImportError:
    logger.warning(
        "python-bidi is not installed — Hebrew text in PDFs will not be rendered RTL correctly."
    )
    def _get_display(text: str) -> str:  # type: ignore[misc]
        return text


def _bidi(text: str) -> str:
    """Apply the Unicode bidi algorithm. A no-op on pure-LTR text, so this is
    always safe to call unconditionally — never gate it on the report's lang.
    A Hebrew report can contain English text (vendor names, descriptions) and
    vice versa; bidi is a property of the content, not the report."""
    if not text:
        return text
    return _get_display(text)


def _xml(text: str) -> str:
    """Escape text for use inside a ReportLab Paragraph (parsed as mini-XML)."""
    import html
    return html.escape(str(text) if text else "")


def _bidi_then_xml(text: str) -> str:
    """Bidi-reorder the raw text FIRST, then XML-escape. Reversing this order
    corrupts any text containing a literal ", &, <, or > — bidi would reorder
    the escaped entity's characters individually instead of the original
    symbol."""
    return _xml(_bidi(text))


# ── Unicode font registration ─────────────────────────────────────────────────
# DejaVu Sans covers Hebrew (U+0590–U+05FF), Latin, ₪ (U+20AA) in one TTF.

_FONT = "ReportFont"
_FONT_BOLD = "ReportFont-Bold"
_FONT_REGISTERED = False

_FONT_CANDIDATES: list[tuple[str, str]] = [
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("FreeSans.ttf", "FreeSansBold.ttf"),
]
_SEARCH_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/freefont",
    "/usr/share/fonts/truetype",
    "/usr/local/share/fonts",
]


def _find_font(filename: str) -> str | None:
    for directory in _SEARCH_DIRS:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            return path
    return None


def _register_font() -> bool:
    """Register the Unicode TTF pair with ReportLab. Returns True if successful."""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return True

    for regular_name, bold_name in _FONT_CANDIDATES:
        regular_path = _find_font(regular_name)
        if not regular_path:
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT, regular_path))
        except Exception as exc:
            logger.warning("Failed to register font %s: %s", regular_name, exc)
            continue

        bold_path = _find_font(bold_name) or regular_path
        try:
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold_path))
        except Exception:
            pass  # bold falls back to regular

        # Register the font family so inline <b> markup (used for Run(bold=True))
        # correctly switches to the bold TTF — ReportLab only auto-resolves
        # <b>/<i> tags for registered font families, otherwise it's a silent no-op.
        pdfmetrics.registerFontFamily(
            _FONT, normal=_FONT, bold=_FONT_BOLD, italic=_FONT_BOLD, boldItalic=_FONT_BOLD
        )

        logger.info("Registered Unicode PDF font: %s", regular_path)
        _FONT_REGISTERED = True
        return True

    logger.warning("No Unicode font found — PDF will use Helvetica (Hebrew will not render)")
    return False


def _font(bold: bool = False) -> str:
    """Return the registered Unicode font name, or Helvetica as fallback."""
    if _register_font():
        return _FONT_BOLD if bold else _FONT
    return "Helvetica-Bold" if bold else "Helvetica"


# ── Colour palette ────────────────────────────────────────────────────────────

_HEADER_BG = colors.HexColor("#1a3c5e")
_HEADER_FG = colors.white
_ALT_ROW_BG = colors.HexColor("#f0f4f8")
_TOTAL_BG = colors.HexColor("#e8f0fe")
_FLAG_BG = colors.HexColor("#fff3cd")
_BORDER_COLOR = colors.HexColor("#c0ccd8")

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm


# ── Cell rendering ────────────────────────────────────────────────────────────

def _run_markup(run: Run, apply_bidi: bool = True) -> str:
    """Bidi+escape one Run's text, then wrap it in ReportLab mini-XML tags."""
    text = _bidi_then_xml(run.text) if apply_bidi else _xml(run.text)
    if run.color:
        text = f'<font color="{run.color}">{text}</font>'
    if run.bold:
        text = f"<b>{text}</b>"
    return text


def _cell_markup(cell: Cell, column: Column) -> str:
    """Turn a Cell (plain string or list[Run]) into ReportLab Paragraph markup.

    "number"-type columns hold already-formatted, always-LTR strings (amounts,
    dates) — bidi is skipped for them. It would be a no-op on such content
    anyway (no strong-RTL characters to reorder), but skipping avoids the
    wasted get_display() call, matching the design doc's stated column-type
    semantics exactly ("number" — "never bidi-processed").
    """
    apply_bidi = column.type != "number"
    if isinstance(cell, str):
        return _bidi_then_xml(cell) if apply_bidi else _xml(cell)
    return "".join(_run_markup(run, apply_bidi=apply_bidi) for run in cell)


def _cell_paragraph(cell: Cell, column: Column, lang: str, row_bold: bool) -> Paragraph:
    rtl = (lang == "he")
    align = TA_RIGHT if (column.type == "number" or rtl) else TA_LEFT
    style = ParagraphStyle(
        f"Cell-{column.type}-{align}-{row_bold}",
        fontName=_font(bold=row_bold),
        fontSize=8,
        leading=10,
        alignment=align,
        wordWrap="RTL" if rtl else "LTR",
    )
    return Paragraph(_cell_markup(cell, column), style)


# ── Section/table building ────────────────────────────────────────────────────

def _column_widths(columns: list[Column], avail_width: float) -> list[float]:
    total_weight = sum(c.width_weight for c in columns) or 1.0
    return [avail_width * c.width_weight / total_weight for c in columns]


def _build_row_cells(row: Row, columns: list[Column], lang: str) -> list[Paragraph]:
    bold = (row.style == "total")
    return [_cell_paragraph(cell, col, lang, bold) for cell, col in zip(row.cells, columns)]


def _build_section_table(section: TableSection, lang: str, avail_width: float) -> Table:
    rtl = (lang == "he")

    header_style = ParagraphStyle(
        "SectionHeader", fontName=_font(bold=True), fontSize=8, leading=10,
        alignment=TA_RIGHT if rtl else TA_LEFT, textColor=colors.white,
    )
    header_cells = [Paragraph(_bidi_then_xml(col.header), header_style) for col in section.columns]
    if rtl:
        header_cells = list(reversed(header_cells))

    table_data = [header_cells]
    flagged_indices: list[int] = []

    for i, row in enumerate(section.rows, start=1):
        if len(row.cells) != len(section.columns):
            raise ValueError(
                f"Row has {len(row.cells)} cells but section has {len(section.columns)} columns"
            )
        cells = _build_row_cells(row, section.columns, lang)
        if rtl:
            cells = list(reversed(cells))
        table_data.append(cells)
        if row.style == "flagged":
            flagged_indices.append(i)

    total_row_index = None
    if section.totals_row is not None:
        if len(section.totals_row.cells) != len(section.columns):
            raise ValueError(
                f"Totals row has {len(section.totals_row.cells)} cells but section has "
                f"{len(section.columns)} columns"
            )
        total_cells = _build_row_cells(section.totals_row, section.columns, lang)
        if rtl:
            total_cells = list(reversed(total_cells))
        table_data.append(total_cells)
        total_row_index = len(table_data) - 1

    col_widths = _column_widths(section.columns, avail_width)
    if rtl:
        col_widths = list(reversed(col_widths))

    last_data_row = (total_row_index - 1) if total_row_index is not None else (len(table_data) - 1)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_FG),
        ("ROWBACKGROUNDS", (0, 1), (-1, last_data_row), [colors.white, _ALT_ROW_BG]),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDER_COLOR),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    # Flagged/total overrides are appended AFTER the base ROWBACKGROUNDS entry
    # so they take precedence for their specific rows (TableStyle commands
    # apply in order; later entries win for the same cell range).
    for idx in flagged_indices:
        style.append(("BACKGROUND", (0, idx), (-1, idx), _FLAG_BG))
    if total_row_index is not None:
        style.append(("BACKGROUND", (0, total_row_index), (-1, total_row_index), _TOTAL_BG))

    t.setStyle(TableStyle(style))
    return t


# ── Document assembly ─────────────────────────────────────────────────────────

def _header_flowables(spec: ReportSpec) -> list:
    rtl = (spec.lang == "he")
    align = TA_RIGHT if rtl else TA_LEFT
    title_style = ParagraphStyle(
        "Title", fontName=_font(bold=True), fontSize=16, leading=20,
        textColor=_HEADER_BG, spaceAfter=4, alignment=align,
    )
    meta_style = ParagraphStyle(
        "Meta", fontName=_font(bold=False), fontSize=9, leading=11,
        textColor=colors.HexColor("#555555"), alignment=align,
    )
    flowables = [Paragraph(_bidi_then_xml(spec.title), title_style)]
    flowables.append(Paragraph(_bidi_then_xml(spec.generated_label), meta_style))
    for line in spec.meta_lines:
        flowables.append(Paragraph(_bidi_then_xml(line), meta_style))
    flowables.append(Spacer(1, 0.5 * cm))
    return flowables


def render_pdf(spec: ReportSpec, extra_flowables: list | None = None) -> bytes:
    """Render a ReportSpec to PDF bytes.

    extra_flowables: already-built ReportLab flowables appended after all
    sections, before the document is finalized. Exists solely so content that
    isn't row/column data (e.g. the invoice image appendix) can land in the
    same PDF document — see the design doc's "The renderer" section for why
    this is a narrow escape hatch, not a general attachment mechanism.
    """
    _register_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=spec.title,
    )

    rtl = (spec.lang == "he")
    story: list = list(_header_flowables(spec))
    avail_width = PAGE_W - 2 * MARGIN

    heading_style = ParagraphStyle(
        "SectionHeading", fontName=_font(bold=True), fontSize=11, leading=14,
        alignment=(TA_RIGHT if rtl else TA_LEFT),
        spaceBefore=8, spaceAfter=4, textColor=_HEADER_BG,
    )
    body_style = ParagraphStyle(
        "Body", fontName=_font(bold=False), fontSize=9, leading=11,
        alignment=(TA_RIGHT if rtl else TA_LEFT),
    )

    for section in spec.sections:
        if section.heading:
            story.append(Paragraph(_bidi_then_xml(section.heading), heading_style))
        if not section.rows and section.empty_message:
            story.append(Paragraph(_bidi_then_xml(section.empty_message), body_style))
        else:
            story.append(_build_section_table(section, spec.lang, avail_width))
        story.append(Spacer(1, 0.4 * cm))

    if extra_flowables:
        story.extend(extra_flowables)

    doc.build(story)
    return buf.getvalue()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_render_pdf.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/requirements.txt orchestrator/app/reports/render_pdf.py orchestrator/tests/test_render_pdf.py
git commit -m "feat: add render_pdf generic PDF renderer (font/bidi/RTL foundation)"
```

---

## Task 4: `render_pdf` — RTL/Hebrew, row styles, Run styling, empty sections

**Files:**
- Modify: `orchestrator/tests/test_render_pdf.py` (append tests — `render_pdf.py` from Task 3 already implements all the behavior these test; this task is verification, not new implementation)

- [ ] **Step 1: Write the failing tests**

Append to `orchestrator/tests/test_render_pdf.py`:

```python
import pytest

from app.reports.spec import Run


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
```

- [ ] **Step 2: Run tests to verify they pass immediately**

Run: `cd orchestrator && python -m pytest tests/test_render_pdf.py -v`
Expected: PASS (7 tests total) — `render_pdf.py` from Task 3 already implements RTL, row styles, Run markup, empty-message, and column-count validation; this task exists to lock in and verify that behavior with explicit tests, per the design doc's testing requirements (RTL/bidi correctness checked by extracted text order, not just byte presence).

If any test fails, the most likely causes and fixes:
- Hebrew/mixed-content test fails on text order → check `_bidi_then_xml` is called on the raw cell string before it reaches `Paragraph`, and that `_build_row_cells` doesn't skip a cell.
- Run-level bold test fails to find "Important" as bold in extracted text (pdfplumber doesn't expose bold/color state directly, so this test only checks the text is present and readable, not its visual bold-ness) → if the text is missing entirely, check `pdfmetrics.registerFontFamily` was called in `_register_font` (Task 3, Step 4) so `<b>` tags don't corrupt the Paragraph's XML parsing.
- Empty-message test finds an empty table instead → check the `if not section.rows and section.empty_message` branch in `render_pdf`'s section loop.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/tests/test_render_pdf.py
git commit -m "test: verify render_pdf RTL, row styles, Run styling, empty sections"
```

---

## Task 5: Migrate `AccountingGenerator`/`generate_ledger_pdf` to `ReportSpec` + `render_pdf`

**Ordering note:** this runs BEFORE the invoice migration (now Task 6), reversing the original draft order. Reason: `accounting_export.py`'s current, unmigrated `generate_ledger_pdf` imports `_bidi`/`_font`/`_register_font`/`_xml` directly from `pdf_report.py`. Stripping `pdf_report.py` down to appendix-only (which the invoice migration does) would break that import while this function is still unmigrated. This task's rewrite removes that dependency entirely (it imports only from `render_pdf.py`/`spec.py`), so once it's done, `pdf_report.py` has no remaining consumers of its old symbols and can be safely stripped in Task 6. No other ordering dependency exists between the two — each blueprint's `ReportSpec`-building code is independent of the other.

**Files:**
- Modify: `orchestrator/app/tools/accounting_export.py` (delete local `_fmt_date`/`_fmt_currency`, rewrite `generate_ledger_pdf`)
- Modify: `orchestrator/tests/test_export_tool.py` (ledger PDF tests)

- [ ] **Step 1: Replace local `_fmt_date`/`_fmt_currency` with imports from `formatting.py`**

In `orchestrator/app/tools/accounting_export.py`, replace lines 16-20 and 59-69 (the `_DATE_FORMATS` dict and the `_fmt_date`/`_fmt_currency` function definitions) with a single import, and update every call site (`_fmt_date(...)` → `format_date(...)`, `_fmt_currency(...)` → `format_currency(...)`) throughout the file — this affects `generate_ledger_xlsx`'s helper functions (`_write_balances_sheet`, `_write_transactions_sheet`, `_write_settlements_sheet`) too, since they currently call the local versions:

```python
# Replace this (lines 16-20):
_DATE_FORMATS = {
    "DD/MM/YYYY": lambda d: d.strftime("%d/%m/%Y") if d else "",
    "YYYY-MM-DD": lambda d: d.isoformat() if d else "",
    "DD MMM YYYY": lambda d: d.strftime("%d %b %Y") if d else "",
}

# ... and this (lines 59-69):
def _fmt_date(d: date_type | None, date_format: str) -> str:
    formatter = _DATE_FORMATS.get(date_format, _DATE_FORMATS["YYYY-MM-DD"])
    return formatter(d)


def _fmt_currency(amount: float, currency_display: str) -> str:
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if currency_display == "₪":
        return f"{sign}₪{amount:.2f}"
    return f"{sign}{amount:.2f} ILS"

# With this single import near the top of the file (after the existing imports):
from app.reports.formatting import format_currency as _fmt_currency
from app.reports.formatting import format_date as _fmt_date
```

Keeping the `_fmt_date`/`_fmt_currency` names as import aliases means every existing call site in `generate_ledger_xlsx`'s helper functions (`_write_balances_sheet`, `_write_transactions_sheet`, `_write_settlements_sheet`) keeps working unchanged — no need to touch the XLSX code at all.

- [ ] **Step 2: Run tests to verify XLSX behavior is unchanged**

Run: `cd orchestrator && python -m pytest tests/test_export_tool.py -k xlsx -v`
Expected: PASS (no XLSX behavior changed, only where the formatting functions are imported from)

- [ ] **Step 3: Rewrite `generate_ledger_pdf` to build a `ReportSpec`**

Replace the entire `generate_ledger_pdf` function (from `def generate_ledger_pdf(` through the end of the file) in `orchestrator/app/tools/accounting_export.py` with:

```python
def generate_ledger_pdf(
    group_jid: str,
    filter_phone: str | None = None,
    fmt_config: dict | None = None,
) -> bytes:
    """Generate a PDF ledger report via the generic render_pdf renderer.

    Layout:
      - Net balances summary: one netted line per pair (who owes whom, net amount).
      - One ledger table per counterparty pair: date | description | {name A} |
        {name B} | comments. Each row's amount is placed under whichever side
        is the from_phone (the one who owed or paid; payments are signed
        negative), with a totals row summing each column so the two sums
        reproduce the net-balance figure above.

    fmt_config keys (from ReportFormat): date_format, currency_display,
    include_settled, sort_by, language.
    """
    from datetime import datetime, timezone
    from app.reports.render_pdf import render_pdf
    from app.reports.spec import Column, ReportSpec, Row, TableSection

    cfg = fmt_config or {}
    date_format: str = cfg.get("date_format", "YYYY-MM-DD")
    currency_display: str = cfg.get("currency_display", "ILS")
    include_settled: bool = cfg.get("include_settled", True)
    sort_by: str = cfg.get("sort_by", "date")

    # Language: explicit fmt_config (call-time override or saved ReportFormat)
    # takes priority; else fall back to GroupConfig.feedback_language.
    lang = cfg.get("language")
    if not lang:
        lang = "en"
        try:
            with SessionLocal() as _db:
                from app.db.models import GroupConfig
                gcfg = _db.get(GroupConfig, group_jid)
                if gcfg and gcfg.feedback_language:
                    lang = gcfg.feedback_language
        except Exception:
            pass

    LABELS = {
        "en": {
            "title": "Family Ledger", "generated": "Generated",
            "net_balances": "Net Balances", "transactions": "Transactions",
            "from": "From", "to": "To", "amount": "Amount (₪)", "date": "Date",
            "description": "Description", "comments": "Comments",
            "settled": "Settled", "payment": "Payment", "remaining": "remaining",
            "total": "Total", "all_settled": "All debts settled.",
            "no_transactions": "No transactions found.",
        },
        "he": {
            "title": "ספר חשבונות משפחתי", "generated": "הופק",
            "net_balances": "יתרות נטו", "transactions": "עסקאות",
            "from": "מ", "to": "ל", "amount": "סכום (₪)", "date": "תאריך",
            "description": "תיאור", "comments": "הערות",
            "settled": "שולם", "payment": "תשלום", "remaining": "נותר",
            "total": "סה״כ", "all_settled": "כל החובות סולקו.",
            "no_transactions": "לא נמצאו עסקאות.",
        },
    }
    L = LABELS.get(lang, LABELS["en"])

    with SessionLocal() as db:
        from app.db.models import LedgerEntry as _LE, HouseholdMember as _HM2
        _member2 = db.query(_HM2).filter_by(private_group_jid=group_jid).first()
        _household_id2 = _member2.household_id if _member2 else None
        query = db.query(_LE)
        if _household_id2:
            query = query.filter(_LE.household_id == _household_id2)
        else:
            query = query.filter(_LE.group_jid == group_jid)
        if filter_phone:
            query = query.filter(or_(
                _LE.from_phone == filter_phone,
                _LE.to_phone == filter_phone,
            ))
        entries = query.order_by(_LE.transaction_date).all()

        phones = {e.from_phone for e in entries} | {e.to_phone for e in entries}
        names = _phone_to_name_from_db(db, group_jid, phones)

    sections = []

    # ── Net balances: one netted line per pair ────────────────────────────────
    net = _compute_net_balances(entries, names)
    if net:
        bal_rows = [
            Row(cells=[frm, to, _fmt_currency(float(amt), currency_display)])
            for (frm, to), amt in sorted(net.items())
        ]
        sections.append(TableSection(
            heading=L["net_balances"],
            columns=[
                Column(header=L["from"]),
                Column(header=L["to"]),
                Column(header=L["amount"], type="number"),
            ],
            rows=bal_rows,
        ))
    else:
        sections.append(TableSection(
            heading=L["net_balances"],
            columns=[Column(header=L["from"]), Column(header=L["to"]), Column(header=L["amount"], type="number")],
            rows=[],
            empty_message=L["all_settled"],
        ))

    # ── Ledger: one table per counterparty pair ───────────────────────────────
    ledger_entries = entries
    if not include_settled:
        ledger_entries = [
            e for e in entries
            if e.entry_type == "payment"
            or (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > 0
        ]

    pairs: dict[tuple[str, str], list] = {}
    for e in ledger_entries:
        key = tuple(sorted((e.from_phone, e.to_phone)))
        pairs.setdefault(key, []).append(e)

    if not pairs:
        sections.append(TableSection(
            heading=L["transactions"],
            columns=[Column(header=L["date"]), Column(header=L["description"]), Column(header=L["comments"])],
            rows=[],
            empty_message=L["no_transactions"],
        ))
    else:
        for i, ((phone_a, phone_b), rows) in enumerate(sorted(
            pairs.items(),
            key=lambda kv: (names.get(kv[0][0], kv[0][0]), names.get(kv[0][1], kv[0][1])),
        )):
            name_a = names.get(phone_a, phone_a)
            name_b = names.get(phone_b, phone_b)

            if sort_by == "amount":
                rows_sorted = sorted(rows, key=lambda e: e.amount_ils, reverse=True)
            else:
                rows_sorted = sorted(rows, key=lambda e: (e.transaction_date, e.created_at or e.transaction_date))

            pair_rows = []
            sum_a = Decimal("0")
            sum_b = Decimal("0")
            for e in rows_sorted:
                date_s = _fmt_date(e.transaction_date, date_format)
                desc_s = (e.description or "")[:60]
                remaining = e.amount_ils - (e.amount_settled_ils or Decimal("0"))

                signed_amount = -e.amount_ils if e.entry_type == "payment" else e.amount_ils
                amt_s = _fmt_currency(float(signed_amount), currency_display)

                if e.entry_type == "payment":
                    comment_s = L["payment"]
                elif remaining <= Decimal("0"):
                    comment_s = L["settled"]
                else:
                    comment_s = f"{_fmt_currency(float(remaining), currency_display)} {L['remaining']}"

                if e.from_phone == phone_a:
                    col_a, col_b = amt_s, ""
                    sum_a += signed_amount
                else:
                    col_a, col_b = "", amt_s
                    sum_b += signed_amount

                pair_rows.append(Row(cells=[date_s, desc_s, col_a, col_b, comment_s]))

            totals_row = Row(
                cells=[
                    "", L["total"],
                    _fmt_currency(float(sum_a), currency_display),
                    _fmt_currency(float(sum_b), currency_display),
                    "",
                ],
                style="total",
            )

            sections.append(TableSection(
                heading=f"{name_a} — {name_b}",
                columns=[
                    Column(header=L["date"]),
                    Column(header=L["description"]),
                    Column(header=name_a, type="number"),
                    Column(header=name_b, type="number"),
                    Column(header=L["comments"]),
                ],
                rows=pair_rows,
                totals_row=totals_row,
            ))

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    spec = ReportSpec(
        title=L["title"],
        lang=lang,
        generated_label=f"{L['generated']}: {generated}",
        sections=sections,
    )
    return render_pdf(spec)
```

Note: the `sum_a`/`sum_b`/`amount` columns are typed `"number"` since they hold already-formatted, always-LTR currency strings, matching the design's column-type semantics (dates and descriptions stay `"text"`, matching original alignment behavior).

- [ ] **Step 4: Update the ledger PDF tests**

The existing tests `test_generate_ledger_pdf_returns_bytes` and `test_generate_ledger_pdf_empty_group_returns_bytes` (lines 169-199 of `test_export_tool.py`) call the real `generate_ledger_pdf` against a test DB and check for valid `%PDF` bytes — these need no changes, since the function's public signature and return type are unchanged. Add one new test that verifies the `ReportSpec` shape directly, appended after `test_generate_ledger_pdf_empty_group_returns_bytes`:

```python
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

    with patch("app.tools.accounting_export.SessionLocal", return_value=_CM(db)), \
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd orchestrator && python -m pytest tests/test_export_tool.py -k ledger -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: PASS, all tests

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/tools/accounting_export.py orchestrator/tests/test_export_tool.py
git commit -m "refactor: migrate generate_ledger_pdf to ReportSpec + render_pdf"
```

---

## Task 6: Migrate `InvoiceGenerator` to `ReportSpec` + `render_pdf`

**Files:**
- Modify: `orchestrator/app/reports/pdf_report.py` (strip to appendix-only)
- Modify: `orchestrator/app/export/generators/invoice.py`
- Modify: `orchestrator/tests/test_export_tool.py` (invoice generator tests)

- [ ] **Step 1: Update `_build_appendix` to import font/bidi from `render_pdf.py` instead of defining locally**

Replace the entire contents of `orchestrator/app/reports/pdf_report.py` with:

```python
"""Invoice image appendix builder — used by InvoiceGenerator when
attach_images=True. Font registration, bidi, and XML-escaping now live in
app.reports.render_pdf (the single canonical implementation); this module
only builds the appendix flowables that get passed to render_pdf's
extra_flowables parameter.
"""
from __future__ import annotations

import logging

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image as RLImage, PageBreak, Paragraph, Spacer, Table, TableStyle

from app.reports.data import ReportRow
from app.reports.labels import get as L
from app.reports.render_pdf import MARGIN, PAGE_H, PAGE_W, _bidi, _font, _xml

logger = logging.getLogger(__name__)

_GRID_COLS = 2   # images per row
_GRID_ROWS = 2   # rows per page  →  4 images per page


def _load_rl_image(row: ReportRow, image_loader, cell_w: float, cell_h: float):
    """Load one invoice image and scale it to fit cell_w × cell_h. Returns RLImage or None."""
    if not row.r2_key:
        return None
    try:
        img_bytes = image_loader(row.r2_key)
    except Exception as exc:
        logger.warning("Could not load image (key=%s): %s", row.r2_key, exc)
        return None

    from PIL import Image as PILImage
    import io as _io
    buf = _io.BytesIO(img_bytes)
    with PILImage.open(buf) as pil_img:
        orig_w, orig_h = pil_img.size

    scale = min(cell_w / orig_w, cell_h / orig_h, 1.0)
    buf.seek(0)
    return RLImage(buf, width=orig_w * scale, height=orig_h * scale)


def build_appendix_flowables(rows: list[ReportRow], lang: str, image_loader) -> list:
    """Build the invoice-image appendix as a list of flowables, to be passed
    as render_pdf's extra_flowables when attach_images=True."""
    story = []
    styles = getSampleStyleSheet()
    rtl = (lang == "he")
    align = TA_RIGHT if rtl else TA_LEFT
    h_align = "RIGHT" if rtl else "LEFT"
    font_normal = _font(bold=False)
    font_bold = _font(bold=True)

    caption_style = ParagraphStyle(
        "AppendixCaption",
        parent=styles["Normal"],
        fontName=font_normal,
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#444444"),
        alignment=align,
    )

    story.append(PageBreak())
    raw_title = L(lang, "appendix_title")
    title_text = _bidi(_xml(raw_title)) if rtl else _xml(raw_title)
    story.append(Paragraph(title_text, ParagraphStyle(
        "AppendixTitle",
        parent=styles["Heading1"],
        fontName=font_bold,
        alignment=align,
    )))
    story.append(Spacer(1, 0.3 * cm))

    cell_gap = 0.3 * cm
    caption_h = 1.0 * cm
    avail_w = PAGE_W - 2 * MARGIN
    avail_h = PAGE_H - 2 * MARGIN - 3.5 * cm
    cell_w = (avail_w - ((_GRID_COLS - 1) * cell_gap)) / _GRID_COLS
    row_h = (avail_h - ((_GRID_ROWS - 1) * cell_gap)) / _GRID_ROWS
    img_cell_h = row_h - caption_h - 0.2 * cm

    cells: list[list] = []
    for row in rows:
        rl_img = _load_rl_image(row, image_loader, cell_w, img_cell_h)
        raw_label = L(lang, "appendix_label", number=row.invoice_number or "—", vendor=row.vendor or "—")
        label_text = _bidi(_xml(raw_label)) if rtl else _xml(raw_label)
        caption = Paragraph(label_text, caption_style)
        cells.append([rl_img, caption])

    if not cells:
        return story

    images_per_page = _GRID_COLS * _GRID_ROWS
    col_widths = [cell_w] * _GRID_COLS

    for page_start in range(0, len(cells), images_per_page):
        page_cells = cells[page_start: page_start + images_per_page]
        while len(page_cells) % _GRID_COLS != 0:
            page_cells.append([None, Paragraph("", caption_style)])

        for row_start in range(0, len(page_cells), _GRID_COLS):
            row_cells = page_cells[row_start: row_start + _GRID_COLS]
            if rtl:
                row_cells = list(reversed(row_cells))

            table_row = []
            for rl_img, caption in row_cells:
                cell_content = [rl_img, Spacer(1, 0.15 * cm), caption] if rl_img else [caption]
                table_row.append(cell_content)

            t = Table([table_row], colWidths=col_widths, rowHeights=[row_h])
            t.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), h_align),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(t)
            story.append(Spacer(1, cell_gap))

        if page_start + images_per_page < len(cells):
            story.append(PageBreak())

    return story
```

Note: `PAGE_W`, `PAGE_H`, `MARGIN` are imported from `render_pdf.py` rather than redefined, so the appendix's page-size math always matches whatever `render_pdf` actually uses.

- [ ] **Step 2: Rewrite `InvoiceGenerator.build_pdf`**

Replace the full contents of `orchestrator/app/export/generators/invoice.py` with:

```python
"""Invoice curator report generator for the export_report tool."""

from __future__ import annotations

from calendar import month_name
from datetime import datetime, timezone

from app.db.models import GroupConfig
from app.db.session import SessionLocal
from app.pipeline.storage import download_image_sync
from app.reports.data import fetch_report_data
from app.reports.excel_report import generate_excel
from app.reports.formatting import format_amount, format_currency, format_date
from app.reports.labels import get as L
from app.reports.pdf_report import build_appendix_flowables
from app.reports.render_pdf import _bidi_then_xml, _font, render_pdf
from app.reports.spec import Column, ReportSpec, Row, TableSection


class NoDataError(Exception):
    pass


def _get_invoice_config(group_jid: str) -> GroupConfig:
    with SessionLocal() as db:
        cfg = db.get(GroupConfig, group_jid)
        if not cfg:
            cfg = GroupConfig(group_id=group_jid)
            db.add(cfg)
            db.commit()
            db.refresh(cfg)
        db.expunge(cfg)
        return cfg


class InvoiceGenerator:
    def __init__(self, group_jid: str):
        self._jid = group_jid

    def _fetch(self, month, year, start_date=None, end_date=None, force_dual_currency=None):
        from datetime import date as _date
        cfg = _get_invoice_config(self._jid)
        dual = force_dual_currency if force_dual_currency is not None else cfg.force_dual_currency
        sd = _date.fromisoformat(start_date) if start_date else None
        ed = _date.fromisoformat(end_date) if end_date else None
        data = fetch_report_data(self._jid, month, year, force_dual_currency=dual, start_date=sd, end_date=ed)
        if not data.rows:
            period = data.period_label or f"{month_name[data.month]} {data.year}"
            raise NoDataError(f"No invoices found for {period}.")
        return data, cfg

    def _period_str(self, data) -> str:
        return data.period_label or f"{month_name[data.month]}_{data.year}"

    def _build_spec(self, data, cfg) -> ReportSpec:
        lang = cfg.feedback_language
        dual = data.show_dual_currency

        # width_weight values reproduce the old generate_pdf's fixed cm widths
        # ([2.4, 2.8, 4.0, 7.0, 3.0] non-dual / [2.2, 2.4, 3.5, 5.5, 2.8, 2.8]
        # dual) as proportions — render_pdf normalizes by total weight, so
        # using the old cm values directly reproduces the same visual ratios.
        columns = [
            Column(header=L(lang, "col_date"), width_weight=2.4 if not dual else 2.2),
            Column(header=L(lang, "col_invoice_no"), width_weight=2.8 if not dual else 2.4),
            Column(header=L(lang, "col_vendor"), width_weight=4.0 if not dual else 3.5),
            Column(header=L(lang, "col_description"), width_weight=7.0 if not dual else 5.5),
        ]
        if dual:
            columns += [
                Column(header=L(lang, "col_amount_orig"), type="number", width_weight=2.8),
                Column(header=L(lang, "col_amount_ils"), type="number", width_weight=2.8),
            ]
        else:
            columns.append(Column(header=L(lang, "col_amount"), type="number", width_weight=3.0))

        rows: list[Row] = []
        for r in data.rows:
            date_s = format_date(r.invoice_date, "DD/MM/YYYY") or "—"
            if r.flagged:
                date_s = f"{date_s} *"
            inv_num = r.invoice_number or "—"
            vendor = r.vendor or "—"
            desc = r.description or "—"

            if dual:
                # amount_original can be any currency (USD, EUR, ILS, ...) -> format_amount.
                # amount_ils is always ILS by definition of this column -> format_currency
                # with the symbol style, matching the old _fmt_ils behavior exactly.
                orig = format_amount(float(r.amount_original) if r.amount_original else None, r.currency_original)
                ils = format_currency(float(r.amount_ils), "₪") if r.amount_ils else "—"
                cells = [date_s, inv_num, vendor, desc, orig, ils]
            else:
                amt = format_amount(float(r.amount_original) if r.amount_original else None, r.currency_original)
                cells = [date_s, inv_num, vendor, desc, amt]

            rows.append(Row(cells=cells, style="flagged" if r.flagged else "normal"))

        n_cols = len(columns)
        total_cells = ["" for _ in range(n_cols)]
        total_cells[n_cols - 2] = L(lang, "total")
        total_cells[n_cols - 1] = format_currency(float(data.total_ils), "₪")
        totals_row = Row(cells=total_cells, style="total")

        period = data.period_label or f"{month_name[data.month]} {data.year}"
        meta_lines = [f"{L(lang, 'period')}: {period}"]
        if cfg.report_author:
            meta_lines.append(f"{L(lang, 'prepared_by')}: {cfg.report_author}")
        generated_label = f"{L(lang, 'generated')}: {datetime.now(timezone.utc).strftime('%d/%m/%Y')}"

        return ReportSpec(
            title=cfg.report_header or L(lang, "report_title_default"),
            lang=lang,
            generated_label=generated_label,
            meta_lines=meta_lines,
            sections=[TableSection(columns=columns, rows=rows, totals_row=totals_row)],
        )

    def build_pdf(
        self,
        month: int | None = None,
        year: int | None = None,
        attach_images: bool = False,
        start_date: str | None = None,
        end_date: str | None = None,
        force_dual_currency: bool | None = None,
    ) -> tuple[bytes, str]:
        data, cfg = self._fetch(month, year, start_date, end_date, force_dual_currency)
        spec = self._build_spec(data, cfg)

        extra_flowables: list = []
        flagged_count = sum(1 for r in data.rows if r.flagged)
        if flagged_count:
            from reportlab.lib.styles import ParagraphStyle
            note_style = ParagraphStyle(
                "FlaggedNote", fontName=_font(bold=False), fontSize=9,
                textColor="#555555", alignment=2 if cfg.feedback_language == "he" else 0,
            )
            from reportlab.platypus import Paragraph
            extra_flowables.append(Paragraph(_bidi_then_xml(L(cfg.feedback_language, "flagged_note")), note_style))

        if attach_images:
            loader = download_image_sync
            extra_flowables.extend(build_appendix_flowables(data.rows, cfg.feedback_language, loader))

        pdf_bytes = render_pdf(spec, extra_flowables=extra_flowables or None)
        filename = f"invoices_{self._period_str(data)}.pdf"
        return pdf_bytes, filename

    def build_xlsx(
        self,
        month: int | None = None,
        year: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        force_dual_currency: bool | None = None,
    ) -> tuple[bytes, str]:
        data, cfg = self._fetch(month, year, start_date, end_date, force_dual_currency)
        xlsx_bytes = generate_excel(
            data,
            lang=cfg.feedback_language,
            title=cfg.report_header or None,
            author=cfg.report_author or None,
        )
        filename = f"invoices_{self._period_str(data)}.xlsx"
        return xlsx_bytes, filename
```

- [ ] **Step 3: Update the invoice generator tests to patch `render_pdf` instead of the deleted `generate_pdf`**

Replace `orchestrator/tests/test_export_tool.py` lines 84-107 (`test_invoice_generator_generate_pdf_returns_bytes`) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd orchestrator && python -m pytest tests/test_export_tool.py -k invoice_generator -v`
Expected: PASS (4 tests: build_pdf, build_pdf flagged, generate_xlsx, no_data_raises — the xlsx and no_data tests are unchanged from before and should still pass since `build_xlsx`/`_fetch` weren't touched)

- [ ] **Step 5: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: PASS, all tests (no regressions in unrelated invoice_curator tests like `test_invoice_tools.py`)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/reports/pdf_report.py orchestrator/app/export/generators/invoice.py orchestrator/tests/test_export_tool.py
git commit -m "refactor: migrate InvoiceGenerator to ReportSpec + render_pdf"
```

---

## Task 7: Production-shaped verification and cleanup

**Files:** None modified — this task verifies the migration end-to-end against real data before considering the migration complete.

- [ ] **Step 1: Run the full local test suite one more time**

Run: `cd orchestrator && python -m pytest -q`
Expected: All tests pass, no warnings about missing modules or unused imports.

- [ ] **Step 2: Confirm no remaining references to deleted functions**

Run: `cd orchestrator && grep -rn "generate_pdf\b" app/ tests/ | grep -v render_pdf`
Expected: No output (the old `generate_pdf` function from `pdf_report.py` is fully deleted and no longer referenced anywhere — `render_pdf` is a different, new name so this grep pattern won't false-positive on it).

Run: `cd orchestrator && grep -rn "_build_table\|_register_font\b" app/reports/pdf_report.py`
Expected: No output (these are now only in `render_pdf.py`).

- [ ] **Step 3: Generate a real invoice PDF against production-shaped data inside the actual container**

This mirrors the verification method already used earlier this session for the ledger PDF redesign (real DejaVu Sans + `python-bidi`, not the local dev machine which lacks both). Write a throwaway script (not committed) that seeds an in-memory-equivalent scenario or queries real invoice data if any exists, calls `InvoiceGenerator.build_pdf()`, and saves the bytes. Copy it into the running orchestrator container via `docker cp`, execute it with `docker compose exec`, copy the resulting PDF back out, and inspect it with the Read tool (which supports PDF rendering) to visually confirm: title, table columns, Hebrew rendering (if any group uses `lang=he`), flagged-row highlighting, and totals row all look correct and match (or intentionally, minimally differ from — see Task 6 Step 2's header-layout simplification) the pre-migration output.

- [ ] **Step 4: Generate a real ledger PDF against the current household `00001` (Eran/Sivan) data the same way**

Same method as Step 3, calling `AccountingGenerator.build_pdf()` / `generate_ledger_pdf()` for group `120363428811325130@g.us`. Confirm: net balances section, per-pair section(s), Hebrew descriptions render correctly, payment rows show a `-` sign, totals row is bold/tinted, and the numbers match what a manual computation against the current ledger_entries would produce (cross-check against the household-scoped `_compute_net_balances` output, same verification approach used earlier this session).

- [ ] **Step 5: Clean up any throwaway verification scripts from the container**

Run (adjust the exact filenames used in Steps 3-4):
```bash
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 "rm -f /tmp/verify_invoice_pdf.py /tmp/verify_ledger_pdf.py /tmp/verify_invoice.pdf /tmp/verify_ledger.pdf"
```

---

## Task 8: Deploy

**Files:** None modified — this is the deploy step, following the established workflow (push to GitHub, then SSH + `docker compose up --build -d` on the Hetzner server; never deploy directly from local).

- [ ] **Step 1: Push all commits from Tasks 1-6**

```bash
git push
```

- [ ] **Step 2: Deploy to production**

```bash
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 "cd /opt/whatsapp && git pull && docker compose up --build -d"
```

- [ ] **Step 3: Confirm both containers are healthy**

Run: `ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 "cd /opt/whatsapp && docker compose ps"`
Expected: `whatsapp-orchestrator-1` and `whatsapp-bridge-1` both show `running`/`healthy`.

- [ ] **Step 4: Re-run Task 7 Steps 3-4 against the newly deployed container** to confirm the deployed build behaves identically to what was verified pre-deploy.

---

## Self-review notes

- **Spec coverage:** `ReportSpec`/`Column`/`Row`/`TableSection`/`Run` (Task 1) ✓; `format_date`/`format_currency`/`format_amount` consolidation (Task 2) ✓; `render_pdf` owning font/bidi/RTL unconditionally with correct escape ordering (Task 3-4) ✓; `extra_flowables` escape hatch for the invoice appendix (Task 6) ✓; `sections` scoped to `TableSection`-only per the design's explicit note (Tasks 3-6, no second section type introduced) ✓; row styles `total`/`flagged` (Task 4) ✓; both blueprints migrated in the same pass, old code deleted with no fallback (Tasks 5-6) ✓; the invoice PDF's latent bidi/escape-order bug fixed as a byproduct of migration, not a separate fix (Task 6 — `render_pdf`'s `_bidi_then_xml` replaces the old buggy `_t()` outright) ✓.
- **Type consistency:** `render_pdf(spec, extra_flowables=None)` signature matches its use in `InvoiceGenerator.build_pdf` (Task 6) and `generate_ledger_pdf` (Task 5, called with no `extra_flowables` — ledger reports never have an appendix, matching the design doc). `Column.type` values (`"text"`/`"number"`) are used consistently across both migrated blueprints. `Row.style` values (`"normal"`/`"total"`/`"flagged"`) match between `spec.py`'s `Literal` definition and both blueprints' usage.
- **Bug caught during self-review:** the first draft of this plan conflated two functionally-different formatters — `accounting_export.py`'s original `_fmt_currency` (ILS-only, sign-aware, symbol-vs-suffix *display preference*) and `pdf_report.py`'s original `_fmt_amount`/`_fmt_ils` (multi-currency, thousands-separated, no sign, symbol only for ILS specifically). Task 2 now defines both `format_currency` (accounting's need) and `format_amount` (invoice's need) as separate functions with distinct docstrings explaining when to use which; Task 6's `InvoiceGenerator` code and test assertions were corrected to call `format_amount` for the multi-currency `amount_original` column and `format_currency(..., "₪")` only for the ILS-specific `amount_ils` column. Also caught: Task 5's rewritten `generate_ledger_pdf` used `datetime`/`timezone` without importing them (the original code had this as a local import inside the function) — added back.
- **Ordering fix (caught mid-implementation, not during the original self-review):** the original draft ran the invoice migration before the accounting migration. This broke, because the unmigrated `generate_ledger_pdf` still imported font/bidi symbols directly from `pdf_report.py`, and the invoice migration strips that file down to appendix-only. Swapped the execution order (accounting first, invoice second) — see the ordering note at the top of Task 5. No code content changed, only sequencing.
- **No placeholders:** every step above contains complete, runnable code — no TBD/TODO markers, no "similar to Task N" references without the actual code repeated in full.
