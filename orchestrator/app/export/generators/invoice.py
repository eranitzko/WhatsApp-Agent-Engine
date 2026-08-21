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
from app.utils.invoice_amount import to_float_or_none


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

    def _build_spec(self, data, cfg, language: str | None = None) -> ReportSpec:
        lang = language or cfg.feedback_language
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
                # format_amount already returns "—" for a None amount, so
                # to_float_or_none can be passed straight through. format_currency
                # does NOT guard None itself, so its call keeps an explicit check.
                orig = format_amount(to_float_or_none(r.amount_original), r.currency_original)
                amount_ils_f = to_float_or_none(r.amount_ils)
                ils = format_currency(amount_ils_f, "₪") if amount_ils_f is not None else "—"
                cells = [date_s, inv_num, vendor, desc, orig, ils]
            else:
                amt = format_amount(to_float_or_none(r.amount_original), r.currency_original)
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
        language: str | None = None,
    ) -> tuple[bytes, str]:
        data, cfg = self._fetch(month, year, start_date, end_date, force_dual_currency)
        lang = language or cfg.feedback_language
        spec = self._build_spec(data, cfg, language=lang)

        extra_flowables: list = []
        flagged_count = sum(1 for r in data.rows if r.flagged)
        if flagged_count:
            from reportlab.lib.styles import ParagraphStyle
            note_style = ParagraphStyle(
                "FlaggedNote", fontName=_font(bold=False), fontSize=9,
                textColor="#555555", alignment=2 if lang == "he" else 0,
            )
            from reportlab.platypus import Paragraph
            extra_flowables.append(Paragraph(_bidi_then_xml(L(lang, "flagged_note")), note_style))

        if attach_images:
            loader = download_image_sync
            extra_flowables.extend(build_appendix_flowables(data.rows, lang, loader))

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
        language: str | None = None,
    ) -> tuple[bytes, str]:
        data, cfg = self._fetch(month, year, start_date, end_date, force_dual_currency)
        xlsx_bytes = generate_excel(
            data,
            lang=language or cfg.feedback_language,
            title=cfg.report_header or None,
            author=cfg.report_author or None,
        )
        filename = f"invoices_{self._period_str(data)}.xlsx"
        return xlsx_bytes, filename
