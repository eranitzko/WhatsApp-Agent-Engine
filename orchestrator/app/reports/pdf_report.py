"""PDF report generation using ReportLab.

Produces:
  - Header: title, period, author, generated date
  - Line items table: date | invoice# | vendor | description | amount(s)
  - Totals row
  - Optional image appendix: invoice photos packed per page, labelled

Font: DejaVu Sans (Unicode — covers Hebrew, Latin, ₪, ₿ in one face).
Hebrew mode: RTL layout, reversed column order, bidi text.
"""

from __future__ import annotations

import io
import logging
import os
from calendar import month_name
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.data import ReportData, ReportRow
from app.reports.labels import get as L

logger = logging.getLogger(__name__)

# ── bidi import ───────────────────────────────────────────────────────────────

try:
    from bidi.algorithm import get_display as _get_display
except ImportError:
    logging.warning(
        "python-bidi is not installed — Hebrew text in PDFs will not be rendered RTL correctly."
    )
    def _get_display(text: str) -> str:  # type: ignore[misc]
        return text

# ── Unicode font registration ─────────────────────────────────────────────────
# DejaVu Sans covers Hebrew (U+0590–U+05FF), Latin, ₪ (U+20AA), ₿ (U+20BF),
# and most other common scripts in a single TTF — no font-stacking needed.
# FreeSans is listed as a fallback in case DejaVu is somehow absent.

_FONT      = "ReportFont"
_FONT_BOLD = "ReportFont-Bold"
_FONT_REGISTERED = False

_FONT_CANDIDATES: list[tuple[str, str]] = [
    ("DejaVuSans.ttf",  "DejaVuSans-Bold.ttf"),
    ("FreeSans.ttf",    "FreeSansBold.ttf"),
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

        logger.info("Registered Unicode PDF font: %s", regular_path)
        _FONT_REGISTERED = True
        return True

    logger.warning("No Unicode font found — PDF will use Helvetica (Hebrew will not render)")
    return False


def _bidi(text: str) -> str:
    """Apply Unicode bidi algorithm so Hebrew renders correctly in ReportLab."""
    if not text:
        return text
    return _get_display(text)


def _xml(text: str) -> str:
    """Escape text for use inside a ReportLab Paragraph (parsed as XML)."""
    import html
    return html.escape(str(text) if text else "")


# ── Colour palette ────────────────────────────────────────────────────────────
_HEADER_BG    = colors.HexColor("#1a3c5e")
_HEADER_FG    = colors.white
_ALT_ROW_BG   = colors.HexColor("#f0f4f8")
_FLAG_BG      = colors.HexColor("#fff3cd")
_TOTAL_BG     = colors.HexColor("#e8f0fe")
_BORDER_COLOR = colors.HexColor("#c0ccd8")

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm


# ── Helpers ───────────────────────────────────────────────────────────────────

_HE_MONTHS = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]


def _period_label(month: int, year: int, lang: str = "en") -> str:
    if lang == "he":
        return f"{_HE_MONTHS[month]} {year}"
    return f"{month_name[month]} {year}"


def _fmt_amount(amount, currency: str | None) -> str:
    if amount is None:
        return "—"
    if currency == "ILS":
        return f"₪{float(amount):,.2f}"
    return f"{float(amount):,.2f} {currency or ''}"


def _fmt_ils(amount) -> str:
    if amount is None:
        return "—"
    return f"₪{float(amount):,.2f}"


def _font(lang: str = "", bold: bool = False) -> str:
    """Return the registered Unicode font name, or Helvetica as fallback."""
    if _register_font():
        return _FONT_BOLD if bold else _FONT
    return "Helvetica-Bold" if bold else "Helvetica"


# ── Table builder ─────────────────────────────────────────────────────────────

def _build_table(data: ReportData, lang: str) -> Table:
    from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
    dual = data.show_dual_currency
    rtl  = (lang == "he")

    font_normal = _font(lang, bold=False)
    font_bold   = _font(lang, bold=True)
    text_align_enum = TA_RIGHT if rtl else TA_LEFT

    _style_cache: dict[str, ParagraphStyle] = {}

    def _p(text: str, bold: bool = False, align=None, color=colors.black) -> Paragraph:
        """Wrap text in a Paragraph so it wraps within cell boundaries."""
        a = align if align is not None else text_align_enum
        name = f"Cell{'Bold' if bold else ''}{'R' if a == TA_RIGHT else 'L'}{'W' if color == colors.white else ''}"
        style = _style_cache.get(name)
        if style is None:
            style = ParagraphStyle(
                name,
                fontName=font_bold if bold else font_normal,
                fontSize=8,
                leading=10,
                alignment=a,
                wordWrap="RTL" if rtl else "LTR",
                textColor=color,
            )
            _style_cache[name] = style
        return Paragraph(_xml(text), style)

    def _ph(text: str, bold: bool = False, color=colors.black) -> Paragraph:
        """Paragraph for Hebrew/bidi text."""
        return _p(_bidi(text), bold=bold, color=color)

    def _pn(text: str, bold: bool = False, color=colors.black) -> Paragraph:
        """Paragraph for numeric/LTR text — always right-aligned as a number."""
        return _p(text, bold=bold, align=TA_RIGHT, color=color)

    headers = [
        L(lang, "col_date"),
        L(lang, "col_invoice_no"),
        L(lang, "col_vendor"),
        L(lang, "col_description"),
    ]
    if dual:
        headers += [L(lang, "col_amount_orig"), L(lang, "col_amount_ils")]
    else:
        headers.append(L(lang, "col_amount"))

    if rtl:
        header_row = [_ph(h, bold=True, color=colors.white) for h in reversed(headers)]
    else:
        header_row = [_p(h, bold=True, color=colors.white) for h in headers]

    table_data = [header_row]
    flagged_rows: list[int] = []

    for i, row in enumerate(data.rows, start=1):
        date_str = row.invoice_date.strftime("%d/%m/%Y") if row.invoice_date else "—"
        if row.flagged:
            date_str = f"{date_str} *"
            flagged_rows.append(i)

        inv_num = row.invoice_number or "—"
        vendor  = row.vendor or "—"
        desc    = row.description or "—"
        amt     = _fmt_amount(row.amount_original, row.currency_original)
        ils     = _fmt_ils(row.amount_ils)

        if dual:
            cells = [_p(date_str), _p(inv_num), _ph(vendor), _ph(desc), _pn(amt), _pn(ils)]
        else:
            cells = [_p(date_str), _p(inv_num), _ph(vendor), _ph(desc), _pn(amt)]

        if rtl:
            cells = list(reversed(cells))

        table_data.append(cells)

    # Totals row
    n_cols = len(headers)
    total_cells = [_p("") for _ in range(n_cols)]
    if rtl:
        total_cells[0] = _pn(_fmt_ils(data.total_ils), bold=True)
        total_cells[1] = _ph(L(lang, "total"), bold=True)
    else:
        total_cells[n_cols - 2] = _p(L(lang, "total"), bold=True)
        total_cells[n_cols - 1] = _pn(_fmt_ils(data.total_ils), bold=True)

    table_data.append(total_cells)

    # Column widths
    avail = PAGE_W - 2 * MARGIN
    if dual:
        col_w = [2.2*cm, 2.4*cm, 3.5*cm, 5.5*cm, 2.8*cm, 2.8*cm]
    else:
        col_w = [2.4*cm, 2.8*cm, 4.0*cm, 7.0*cm, 3.0*cm]

    if rtl:
        col_w = list(reversed(col_w))

    total_w = sum(col_w)
    col_w = [w * avail / total_w for w in col_w]

    t = Table(table_data, colWidths=col_w, repeatRows=1)

    style = [
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0),  _HEADER_BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  _HEADER_FG),
        # Body alternating rows
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, _ALT_ROW_BG]),
        # Totals row
        ("BACKGROUND",    (0, -1), (-1, -1), _TOTAL_BG),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 0.4, _BORDER_COLOR),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]

    for row_idx in flagged_rows:
        style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), _FLAG_BG))

    t.setStyle(TableStyle(style))
    return t


# ── Image appendix ────────────────────────────────────────────────────────────

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


def _build_appendix(rows: list[ReportRow], lang: str, image_loader) -> list:
    from reportlab.lib.enums import TA_RIGHT, TA_LEFT

    story = []
    styles = getSampleStyleSheet()
    rtl  = (lang == "he")
    align = TA_RIGHT if rtl else TA_LEFT
    h_align = "RIGHT" if rtl else "LEFT"
    font_normal = _font(lang)
    font_bold   = _font(lang, bold=True)

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

    # Grid dimensions
    cell_gap   = 0.3 * cm
    caption_h  = 1.0 * cm   # reserved below each image for caption
    avail_w    = PAGE_W - 2 * MARGIN
    avail_h    = PAGE_H - 2 * MARGIN - 3.5 * cm   # leave room for title + header
    cell_w     = (avail_w - ((_GRID_COLS - 1) * cell_gap)) / _GRID_COLS
    row_h      = (avail_h - ((_GRID_ROWS - 1) * cell_gap)) / _GRID_ROWS
    img_cell_h = row_h - caption_h - 0.2 * cm

    # Load and build all cells
    cells: list[list] = []   # each entry: [rl_img_or_None, caption_paragraph]
    for row in rows:
        rl_img = _load_rl_image(row, image_loader, cell_w, img_cell_h)
        raw_label = L(lang, "appendix_label",
                      number=row.invoice_number or "—",
                      vendor=row.vendor or "—")
        label_text = _bidi(_xml(raw_label)) if rtl else _xml(raw_label)
        caption = Paragraph(label_text, caption_style)
        cells.append([rl_img, caption])

    if not cells:
        return story

    # Pack cells into grid rows, then pages
    images_per_page = _GRID_COLS * _GRID_ROWS
    col_widths = [cell_w] * _GRID_COLS

    for page_start in range(0, len(cells), images_per_page):
        page_cells = cells[page_start: page_start + images_per_page]

        # Pad to full grid
        while len(page_cells) % _GRID_COLS != 0:
            page_cells.append([None, Paragraph("", caption_style)])

        for row_start in range(0, len(page_cells), _GRID_COLS):
            row_cells = page_cells[row_start: row_start + _GRID_COLS]
            if rtl:
                row_cells = list(reversed(row_cells))

            table_row = []
            for rl_img, caption in row_cells:
                if rl_img:
                    cell_content = [rl_img, Spacer(1, 0.15 * cm), caption]
                else:
                    cell_content = [caption]
                table_row.append(cell_content)

            t = Table([table_row], colWidths=col_widths,
                      rowHeights=[row_h])
            t.setStyle(TableStyle([
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                ("ALIGN",        (0, 0), (-1, -1), h_align),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
            ]))
            story.append(t)
            story.append(Spacer(1, cell_gap))

        # Page break between pages (not after the last one)
        if page_start + images_per_page < len(cells):
            story.append(PageBreak())

    return story


# ── Public API ────────────────────────────────────────────────────────────────

def generate_pdf(
    data: ReportData,
    lang: str = "en",
    title: str | None = None,
    author: str | None = None,
    attach_images: bool = False,
    image_loader=None,
) -> bytes:
    """Generate a PDF report and return bytes."""
    rtl = (lang == "he")
    _register_font()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=title or L(lang, "report_title_default"),
        author=author or "",
    )

    styles = getSampleStyleSheet()
    font_normal = _font(lang)
    font_bold   = _font(lang, bold=True)

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=16,
        textColor=_HEADER_BG,
        spaceAfter=4,
        alignment=2 if rtl else 0,  # 2=RIGHT, 0=LEFT
    )
    meta_style = ParagraphStyle(
        "ReportMeta",
        parent=styles["Normal"],
        fontName=font_normal,
        fontSize=9,
        textColor=colors.HexColor("#555555"),
        alignment=2 if rtl else 0,
    )

    period    = getattr(data, "period_label", None) or _period_label(data.month, data.year, lang)
    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    def _t(text: str) -> str:
        return _bidi(_xml(text)) if rtl else _xml(text)

    # Two-column header: title+meta on the main side, date on the opposite corner
    title_text  = _t(title or L(lang, "report_title_default"))
    period_text = _t(f"{L(lang, 'period')}: {period}")
    author_text = _t(f"{L(lang, 'prepared_by')}: {author}") if author else None

    main_block = [Paragraph(title_text, title_style),
                  Paragraph(period_text, meta_style)]
    if author_text:
        main_block.append(Paragraph(author_text, meta_style))

    date_style = ParagraphStyle(
        "ReportDate",
        parent=styles["Normal"],
        fontName=font_normal,
        fontSize=9,
        textColor=colors.HexColor("#555555"),
        alignment=0 if rtl else 2,  # date goes to LEFT in RTL, RIGHT in LTR
    )
    date_block = [Paragraph(_xml(generated), date_style)]

    # Left col = main block in LTR, date block in RTL (and vice versa)
    if rtl:
        hdr_data = [[date_block, main_block]]
        hdr_col_w = [3 * cm, PAGE_W - 2 * MARGIN - 3 * cm]
    else:
        hdr_data = [[main_block, date_block]]
        hdr_col_w = [PAGE_W - 2 * MARGIN - 3 * cm, 3 * cm]

    hdr_table = Table(hdr_data, colWidths=hdr_col_w)
    hdr_table.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    story = [hdr_table, Spacer(1, 0.5 * cm)]

    if not data.rows:
        story.append(Paragraph(_t(L(lang, "no_invoices")), ParagraphStyle(
            "NoInv", parent=styles["Normal"], fontName=font_normal
        )))
    else:
        story.append(_build_table(data, lang))

        flagged_count = sum(1 for r in data.rows if r.flagged)
        if flagged_count:
            story.append(Spacer(1, 0.2 * cm))
            story.append(Paragraph(_t(L(lang, "flagged_note")), meta_style))

        if attach_images and image_loader:
            story.extend(_build_appendix(data.rows, lang, image_loader))

    doc.build(story)
    return buf.getvalue()
