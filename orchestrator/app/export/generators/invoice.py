"""Invoice curator report generator for the export_report tool."""

from __future__ import annotations

from calendar import month_name

from app.db.models import GroupConfig
from app.db.session import SessionLocal
from app.pipeline.storage import download_image_sync
from app.reports.data import fetch_report_data
from app.reports.excel_report import generate_excel
from app.reports.pdf_report import generate_pdf


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
        loader = download_image_sync if attach_images else None
        pdf_bytes = generate_pdf(
            data,
            lang=cfg.feedback_language,
            title=cfg.report_header or None,
            author=cfg.report_author or None,
            attach_images=attach_images,
            image_loader=loader,
        )
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
