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
