"""Tests for app/main.py's pipeline-result-to-WhatsApp-message conversion."""

from app.main import (
    _pipeline_result_to_message,
    _apply_quoted_context,
    _combine_caption_and_pipeline_message,
)


def test_new_invoice_message_includes_extracted_fields():
    result = {
        "duplicate": False, "vendor": "Acme", "amount_original": 100.0,
        "currency_original": "ILS", "invoice_date": "2026-08-08", "invoice_number": "INV-1",
        "flagged": False,
    }
    msg = _pipeline_result_to_message(result)
    assert "Invoice auto-saved" in msg
    assert "Acme" in msg
    assert "100.0" in msg


def test_flagged_invoice_message_includes_reason():
    result = {
        "duplicate": False, "vendor": "Acme", "amount_original": 100.0,
        "currency_original": "ILS", "flagged": True, "flag_reason": "Low confidence",
    }
    msg = _pipeline_result_to_message(result)
    assert "Low confidence" in msg


def test_duplicate_message_id_case_has_no_crash_and_is_informative():
    """Regression: this shape (bare {"duplicate": True, "duplicate_reason":
    "duplicate_message_id"}) has none of existing_vendor/existing_amount/
    existing_date — the old code didn't handle it distinctly at all."""
    result = {"duplicate": True, "duplicate_reason": "duplicate_message_id"}
    msg = _pipeline_result_to_message(result)
    assert msg  # must produce something, not raise or return empty
    assert "already recorded" in msg.lower()


def test_duplicate_amount_date_message_uses_existing_fields_not_missing_keys():
    """Regression: this branch previously read result["vendor"] and
    result["invoice_number"], which don't exist in this response shape
    (pipeline.py's _duplicate_response uses existing_vendor/existing_amount/
    existing_date instead) — so the "message" was always just "Duplicate
    invoice detected:  ." with nothing useful in it."""
    result = {
        "duplicate": True, "duplicate_reason": "same_amount_and_date",
        "existing_invoice_id": "abc-123",
        "existing_vendor": "כולבולית", "existing_amount": "30.00 ILS", "existing_date": "2026-08-08",
        "extracted_vendor": "קולבולית עין חרוד איחוד", "extracted_amount": "30.00 ILS", "extracted_date": "2026-08-08",
    }
    msg = _pipeline_result_to_message(result)
    assert "כולבולית" in msg
    assert "30.00 ILS" in msg
    assert "2026-08-08" in msg


def test_duplicate_identical_image_message_falls_back_gracefully():
    """The identical_image duplicate case also only has existing_* fields
    (no extracted_* — the pipeline returns early before OCR ever runs)."""
    result = {
        "duplicate": True, "duplicate_reason": "identical_image",
        "existing_invoice_id": "abc-123",
        "existing_vendor": "Acme", "existing_amount": "50.00 ILS", "existing_date": "2026-08-01",
    }
    msg = _pipeline_result_to_message(result)
    assert "Acme" in msg
    assert "50.00 ILS" in msg


def test_combine_caption_and_pipeline_message_prepends_caption_when_present():
    """Regression: a caption typed alongside an invoice photo (e.g. an
    explicit language request) was discarded entirely — main.py overwrote
    agent_message with the pipeline summary instead of combining the two,
    so the agent never saw anything the sender actually wrote on an image
    turn."""
    result = _combine_caption_and_pipeline_message(
        "תענה בעברית בבקשה", "Invoice auto-saved — Vendor: Acme"
    )
    assert "תענה בעברית בבקשה" in result
    assert "Invoice auto-saved — Vendor: Acme" in result


def test_combine_caption_and_pipeline_message_uses_pipeline_message_alone_when_no_caption():
    """The common case: a bare photo with no caption — behavior must be
    unchanged from before this fix."""
    result = _combine_caption_and_pipeline_message("", "Invoice auto-saved — Vendor: Acme")
    assert result == "Invoice auto-saved — Vendor: Acme"


def test_apply_quoted_context_prepends_quoted_text():
    """Regression: replying (WhatsApp's quote/reply feature) to an earlier
    message with a bare word like "לאשר" (approve) reached the agent with
    zero context — the bridge silently dropped contextInfo.quotedMessage, so
    the agent had nothing to approve and said so. Once the bridge forwards
    the quoted text, main.py must fold it into the agent's message."""
    result = _apply_quoted_context("לאשר", "העברתי 570 לעדן")
    assert "העברתי 570 לעדן" in result
    assert "לאשר" in result


def test_apply_quoted_context_passes_through_when_no_quote():
    assert _apply_quoted_context("hello", None) == "hello"
    assert _apply_quoted_context("hello", "") == "hello"
