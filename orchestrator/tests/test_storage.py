"""Tests for app/pipeline/storage.py's R2 invoice sidecar dict builder."""


def test_invoice_to_sidecar_dict_has_all_18_fields():
    """Regression: both pipeline.py (initial ingestion) and storage.py
    (post-correction re-sync) must build the sidecar from this one function,
    not two hand-written, independently-maintained dicts that must
    coincidentally stay field-for-field identical."""
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from app.db.models import Invoice
    from app.pipeline.storage import invoice_to_sidecar_dict

    invoice = Invoice(
        id="inv-1", group_id="123@g.us", message_id="msg-1", image_hash="hash-1",
        submitted_by="972501@s.whatsapp.net",
        received_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        invoice_date=date(2026, 7, 14), invoice_number="INV-1", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"), exchange_rate=Decimal("1"), rate_source="boi",
        extraction_confidence=0.9, flagged=False, flag_reason=None,
    )
    d = invoice_to_sidecar_dict(invoice)
    assert d["invoice_id"] == "inv-1"
    assert d["amount_original"] == 100.0
    assert d["invoice_date"] == "2026-07-14"
    assert d["received_at"] == "2026-07-14T00:00:00+00:00"
    assert set(d.keys()) == {
        "invoice_id", "group_id", "message_id", "image_hash", "submitted_by",
        "received_at", "invoice_date", "invoice_number", "vendor", "description",
        "amount_original", "currency_original", "amount_ils", "exchange_rate",
        "rate_source", "extraction_confidence", "flagged", "flag_reason",
    }
