from app.pipeline.extractor import _validate_and_normalise, _build_prompt, _EXTRACTION_PROMPT, extract_invoice


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


def test_validate_and_normalise_two_digit_year_parsed_via_shared_engine():
    """Regression: a 2-digit-year date must parse without requiring an
    admin-configured extra format — the shared date_formats.py engine
    already supports 2-digit years; extractor.py's own hardcoded regexes
    didn't, silently failing extraction instead."""
    raw = {"invoice_date": "14/07/26", "vendor": "X", "amount_original": 50, "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["invoice_date"] == "2026-07-14"


def test_build_prompt_includes_custom_instructions_when_provided():
    """Regression: admin-configured group hints (GroupRegistry.custom_instructions)
    must reach Gemini's own extraction prompt, not just Claude's — otherwise an
    admin's fix for a vendor-specific quirk (date format, currency, etc.) silently
    does nothing to the OCR step that actually reads the image."""
    prompt = _build_prompt("Our receipts print amounts in USD, not ILS.")
    assert "Our receipts print amounts in USD, not ILS." in prompt
    assert "invoice_date must be output in YYYY-MM-DD format" in prompt  # base rules preserved


def test_build_prompt_unchanged_when_no_custom_instructions():
    assert _build_prompt("") == _EXTRACTION_PROMPT
    assert _build_prompt(None) == _EXTRACTION_PROMPT
    assert _build_prompt("   ") == _EXTRACTION_PROMPT


async def test_extract_invoice_forwards_custom_instructions_to_gemini(monkeypatch):
    captured = {}

    class FakeResponse:
        text = (
            '{"invoice_date": "2026-07-14", "vendor": "X", "amount_original": 10, '
            '"currency_original": "ILS", "confidence": 0.9}'
        )

    class FakeModel:
        def generate_content(self, parts, **kwargs):
            captured["prompt"] = parts[0]
            return FakeResponse()

    monkeypatch.setattr("app.pipeline.extractor.genai.GenerativeModel", lambda *a, **k: FakeModel())

    result = await extract_invoice(b"fake-bytes", "image/jpeg", custom_instructions="Vendor X always uses USD.")
    assert "Vendor X always uses USD." in captured["prompt"]
    assert result["vendor"] == "X"
