from datetime import datetime, timezone, date, timedelta
from decimal import Decimal

import pytest

from app.db.models import Invoice, LedgerEntry, LedgerSettlement
from app.automation.evaluators import ThresholdEvaluator


def _add_invoice(db, group_id, amount_ils, invoice_date=None):
    from app.db.models import Invoice
    inv = Invoice(
        group_id=group_id,
        message_id=f"msg-{amount_ils}-{invoice_date}",
        image_hash=f"hash-{amount_ils}-{invoice_date}",
        amount_ils=Decimal(str(amount_ils)),
        currency_original="ILS",
        invoice_date=invoice_date or date.today(),
    )
    db.add(inv)
    db.commit()


def _add_ledger_entry(db, group_jid, from_phone, to_phone, amount_ils, amount_settled=0):
    entry = LedgerEntry(
        transaction_id="tx-1",
        group_jid=group_jid,
        from_phone=from_phone,
        to_phone=to_phone,
        amount_ils=Decimal(str(amount_ils)),
        amount_settled_ils=Decimal(str(amount_settled)),
        description="test",
        transaction_date=date.today(),
    )
    db.add(entry)
    db.commit()
    return entry


def test_monthly_invoice_total_sums_current_month(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 100, date.today())
    _add_invoice(db, "123@g.us", 250, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(350.0)


def test_monthly_invoice_total_excludes_other_groups(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 100, date.today())
    _add_invoice(db, "999@g.us", 9999, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(100.0)


def test_monthly_invoice_total_excludes_previous_months(db):
    ev = ThresholdEvaluator()
    last_month = date.today().replace(day=1) - timedelta(days=1)
    _add_invoice(db, "123@g.us", 500, last_month)
    _add_invoice(db, "123@g.us", 100, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(100.0)


def test_invoice_count_this_month(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 10, date.today())
    _add_invoice(db, "123@g.us", 20, date.today())
    _add_invoice(db, "123@g.us", 30, date.today())
    result = ev.evaluate(db, "123@g.us", "invoice_count_this_month")
    assert result == 3.0


def test_open_debt_amount_sums_unsettled(db):
    ev = ThresholdEvaluator()
    _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=500, amount_settled=200)
    _add_ledger_entry(db, "123@g.us", "333", "222", amount_ils=300, amount_settled=0)
    result = ev.evaluate(db, "123@g.us", "open_debt_amount")
    assert result == pytest.approx(600.0)  # (500-200) + (300-0)


def test_open_debt_amount_ignores_fully_settled(db):
    ev = ThresholdEvaluator()
    _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=100, amount_settled=100)
    result = ev.evaluate(db, "123@g.us", "open_debt_amount")
    assert result == pytest.approx(0.0)


def test_days_since_last_settlement(db):
    ev = ThresholdEvaluator()
    entry = _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=100, amount_settled=50)
    settlement = LedgerSettlement(
        payment_leg_id=entry.id,
        debt_leg_id=entry.id,
        amount_ils=Decimal("50"),
    )
    # Force created_at to 3 days ago
    settlement.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.add(settlement)
    db.commit()
    result = ev.evaluate(db, "123@g.us", "days_since_last_settlement")
    assert 2.9 < result < 3.1


def test_days_since_last_settlement_returns_inf_when_no_settlements(db):
    ev = ThresholdEvaluator()
    result = ev.evaluate(db, "123@g.us", "days_since_last_settlement")
    assert result == float("inf")


def test_unknown_metric_raises_value_error(db):
    ev = ThresholdEvaluator()
    with pytest.raises(ValueError, match="Unknown metric"):
        ev.evaluate(db, "123@g.us", "nonexistent_metric")
