"""Tests for app/pipeline/pipeline.py's process_image_event, focused on the
extraction-failure placeholder path (previously a photo was permanently lost
on any OCR error, despite a comment claiming a placeholder was persisted)."""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from decimal import Decimal

from app.db.models import Invoice
from app.pipeline.converter import ConversionResult
from app.pipeline.pipeline import process_image_event
from tests.conftest import SessionCM, make_invoice

_FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-testing"


def _event(**overrides) -> dict:
    base = {
        "jid": "123@g.us",
        "sender": "972501234567@s.whatsapp.net",
        "messageId": "MSG-1",
        "imageBase64": base64.b64encode(_FAKE_IMAGE_BYTES).decode(),
        "mimeType": "image/jpeg",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_ocr_failure_persists_flagged_placeholder_with_image(db):
    """Regression: previously returned {"error": ...} with the photo bytes
    only ever having existed in memory — nothing was saved despite a
    comment claiming otherwise. Now a flagged placeholder row must exist,
    with the image still uploaded to R2."""
    with patch("app.pipeline.pipeline.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.pipeline.extract_invoice", AsyncMock(return_value={"error": "Gemini timed out"})), \
         patch("app.pipeline.pipeline.upload_image", AsyncMock(return_value="invoices/123@g.us/fake-key.jpg")), \
         patch("app.pipeline.pipeline.upload_metadata", AsyncMock(return_value="invoices/123@g.us/fake-key.json")):
        result = await process_image_event(_event())

    assert "error" not in result
    assert result["flagged"] is True
    assert "Gemini timed out" in result["flag_reason"]
    assert result["r2_key"] == "invoices/123@g.us/fake-key.jpg"
    assert result["vendor"] is None

    saved = db.query(Invoice).filter_by(group_id="123@g.us").one()
    assert saved.flagged is True
    assert saved.r2_key == "invoices/123@g.us/fake-key.jpg"
    assert saved.vendor is None
    assert saved.amount_original is None


@pytest.mark.asyncio
async def test_ocr_failure_still_persists_row_when_r2_upload_also_fails(db):
    """Even if R2 is also having trouble, the flagged placeholder row itself
    (with no image) is better than losing the event entirely — an admin
    can still see something was received and manually investigate."""
    with patch("app.pipeline.pipeline.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.pipeline.extract_invoice", AsyncMock(return_value={"error": "Gemini timed out"})), \
         patch("app.pipeline.pipeline.upload_image", AsyncMock(side_effect=RuntimeError("R2 is down"))):
        result = await process_image_event(_event())

    assert "error" not in result
    assert result["flagged"] is True
    assert result["r2_key"] is None

    saved = db.query(Invoice).filter_by(group_id="123@g.us").one()
    assert saved.r2_key is None
    assert saved.flagged is True


@pytest.mark.asyncio
async def test_ocr_failure_respects_duplicate_message_id(db):
    """A bridge retry/replay of the same message must not create a second
    placeholder row for a message_id already recorded."""
    make_invoice(db, group_id="123@g.us", message_id="MSG-1")

    with patch("app.pipeline.pipeline.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.pipeline.extract_invoice", AsyncMock(return_value={"error": "Gemini timed out"})), \
         patch("app.pipeline.pipeline.upload_image", AsyncMock(return_value="irrelevant")):
        result = await process_image_event(_event(messageId="MSG-1"))

    assert result.get("duplicate") is True
    assert result["duplicate_reason"] == "duplicate_message_id"
    assert db.query(Invoice).filter_by(group_id="123@g.us").count() == 1


@pytest.mark.asyncio
async def test_successful_extraction_runs_upload_and_conversion_concurrently(db):
    """Performance change: R2 upload and currency conversion are now run via
    asyncio.gather instead of sequentially, since neither depends on the
    other's result. Verify both results are still correctly wired into the
    saved invoice and returned result — a behavior-preserving refactor, not
    just a speed change."""
    extraction = {
        "vendor": "Acme", "invoice_number": "INV-1", "description": "Widgets",
        "amount_original": 100.0, "currency_original": "USD",
        "invoice_date": "2026-08-08", "confidence": 0.95,
    }
    conversion = ConversionResult(
        amount_ils=Decimal("370.00"), exchange_rate=Decimal("3.7"),
        rate_source="boi", rate_date=None,
    )

    with patch("app.pipeline.pipeline.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.pipeline.extract_invoice", AsyncMock(return_value=extraction)), \
         patch("app.pipeline.pipeline.upload_image", AsyncMock(return_value="invoices/123@g.us/key.jpg")), \
         patch("app.pipeline.pipeline.upload_metadata", AsyncMock(return_value="invoices/123@g.us/key.json")), \
         patch("app.pipeline.pipeline.convert_to_ils", AsyncMock(return_value=conversion)):
        result = await process_image_event(_event())

    assert result["vendor"] == "Acme"
    assert result["r2_key"] == "invoices/123@g.us/key.jpg"
    assert result["amount_ils"] == 370.0
    assert result["exchange_rate"] == 3.7
    assert result["flagged"] is False

    saved = db.query(Invoice).filter_by(group_id="123@g.us").one()
    assert saved.vendor == "Acme"
    assert saved.r2_key == "invoices/123@g.us/key.jpg"
    assert saved.amount_ils == Decimal("370.00")
