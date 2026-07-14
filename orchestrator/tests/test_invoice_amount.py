from decimal import Decimal

from app.utils.invoice_amount import validate_invoice_amount


def test_validate_invoice_amount_positive():
    result = validate_invoice_amount(95.2)
    assert result.amount == Decimal("95.2")
    assert result.error is None


def test_validate_invoice_amount_negative_is_valid():
    result = validate_invoice_amount(-22.5)
    assert result.amount == Decimal("-22.5")
    assert result.error is None


def test_validate_invoice_amount_zero_rejected():
    result = validate_invoice_amount(0)
    assert result.amount is None
    assert result.error == "Amount cannot be zero."


def test_validate_invoice_amount_none_rejected():
    result = validate_invoice_amount(None)
    assert result.amount is None
    assert "invalid" in result.error.lower()


def test_validate_invoice_amount_non_numeric_rejected():
    result = validate_invoice_amount("not a number")
    assert result.amount is None
    assert "invalid" in result.error.lower()
