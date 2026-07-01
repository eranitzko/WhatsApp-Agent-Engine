"""Family accounting report generator for the export_report tool."""

from __future__ import annotations

from app.tools.accounting_export import generate_ledger_pdf, generate_ledger_xlsx


class AccountingGenerator:
    def __init__(self, group_jid: str, filter_phone: str | None = None):
        self._jid = group_jid
        self._filter = filter_phone

    def build_pdf(self, fmt_config: dict | None = None) -> tuple[bytes, str]:
        data = generate_ledger_pdf(self._jid, filter_phone=self._filter, fmt_config=fmt_config or {})
        return data, "ledger.pdf"

    def build_xlsx(self, fmt_config: dict | None = None) -> tuple[bytes, str]:
        data = generate_ledger_xlsx(self._jid, filter_phone=self._filter, fmt_config=fmt_config or {})
        return data, "ledger.xlsx"
