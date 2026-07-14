from app.pipeline.extractor import _validate_and_normalise


def test_validate_and_normalise_negative_amount_kept_for_refund():
    """Regression: a refund/return receipt with a negative extracted amount
    used to be silently nulled out (indistinguishable from a genuine
    extraction failure) because amount_original required strictly > 0."""
    raw = {
        "invoice_date": "2026-07-14", "vendor": "כולבולית עין חרוד איחוד",
        "amount_original": -22.5, "currency_original": "ILS", "confidence": 0.9,
    }
    out = _validate_and_normalise(raw)
    assert out["amount_original"] == -22.5


def test_validate_and_normalise_zero_amount_treated_as_missing():
    raw = {"invoice_date": "2026-07-14", "vendor": "X", "amount_original": 0, "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["amount_original"] is None


def test_validate_and_normalise_positive_amount_still_works():
    raw = {"invoice_date": "2026-07-14", "vendor": "X", "amount_original": 95.2, "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["amount_original"] == 95.2


def test_validate_and_normalise_null_amount_stays_none():
    raw = {"invoice_date": "2026-07-14", "vendor": "X", "amount_original": None, "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["amount_original"] is None


def test_validate_and_normalise_non_numeric_amount_stays_none():
    raw = {"invoice_date": "2026-07-14", "vendor": "X", "amount_original": "not a number", "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["amount_original"] is None
