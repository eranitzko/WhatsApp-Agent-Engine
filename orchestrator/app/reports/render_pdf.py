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
        # Always "LTR": _cell_markup already bidi-reordered the text into final
        # visual order via python-bidi. ReportLab's own wordWrap="RTL" performs
        # a SECOND, independent bidi/line-break pass on top of that — for a
        # narrow cell this double-processing corrupts pure-LTR content with
        # trailing punctuation (e.g. "Ben & Jerry's" -> "s'Ben & Jerry").
        # Alignment (TA_RIGHT above) handles the visual right-justification;
        # wordWrap must stay LTR since the text handed to Paragraph is already
        # in display order, not logical order.
        wordWrap="LTR",
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
