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
