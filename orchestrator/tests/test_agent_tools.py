"""Tests for app/agent/tools.py's exec_list_invoices total_ils field.

Regression: list_invoices previously returned only the raw per-invoice rows,
leaving the agent to sum them itself in reply text — observed in production
to produce an arithmetic error on a ~20-row month. total_ils is now computed
server-side (same approach as exec_get_preview) so the agent can relay a
verified number instead of doing the math itself.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.agent.tools import exec_list_invoices
from tests.conftest import SessionCM, make_invoice


@pytest.mark.asyncio
async def test_list_invoices_total_ils_matches_sum_of_rows(db):
    make_invoice(db, group_id="123@g.us", invoice_date=date(2026, 9, 1), amount_ils=Decimal("164.10"))
    make_invoice(db, group_id="123@g.us", invoice_date=date(2026, 9, 2), amount_ils=Decimal("399.00"))
    make_invoice(db, group_id="123@g.us", invoice_date=date(2026, 9, 3), amount_ils=Decimal("41.90"))

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)):
        result = await exec_list_invoices(group_id="123@g.us", month=9, year=2026)

    assert result["count"] == 3
    assert result["total_ils"] == pytest.approx(605.00)


@pytest.mark.asyncio
async def test_list_invoices_total_ils_ignores_rows_without_ils_amount(db):
    make_invoice(db, group_id="123@g.us", invoice_date=date(2026, 9, 1), amount_ils=Decimal("100"))
    make_invoice(db, group_id="123@g.us", invoice_date=date(2026, 9, 2), amount_ils=None, vendor=None,
                 amount_original=None, currency_original=None)

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)):
        result = await exec_list_invoices(group_id="123@g.us", month=9, year=2026)

    assert result["count"] == 2
    assert result["total_ils"] == pytest.approx(100.0)
