"""Tests for app/pipeline/storage.py's R2 invoice sidecar dict builder and
image resize helper."""

import io
from datetime import date, datetime, timezone
from decimal import Decimal

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Invoice
from app.pipeline.storage import invoice_to_sidecar_dict, resize_image


def test_invoice_to_sidecar_dict_has_all_19_fields():
    """Regression: both pipeline.py (initial ingestion) and storage.py
    (post-correction re-sync) must build the sidecar from this one function,
    not two hand-written, independently-maintained dicts that must
    coincidentally stay field-for-field identical."""
    invoice = Invoice(
        id="inv-1", group_id="123@g.us", message_id="msg-1", image_hash="hash-1",
        perceptual_hash="phash-1",
        submitted_by="972501@s.whatsapp.net",
        received_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        invoice_date=date(2026, 7, 14), invoice_number="INV-1", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"), exchange_rate=Decimal("1"), rate_source="boi",
        extraction_confidence=0.9, flagged=False, flag_reason=None,
    )
    d = invoice_to_sidecar_dict(invoice)
    assert d["invoice_id"] == "inv-1"
    assert d["perceptual_hash"] == "phash-1"
    assert d["amount_original"] == 100.0
    assert d["invoice_date"] == "2026-07-14"
    assert d["received_at"] == "2026-07-14T00:00:00+00:00"
    assert set(d.keys()) == {
        "invoice_id", "group_id", "message_id", "image_hash", "perceptual_hash", "submitted_by",
        "received_at", "invoice_date", "invoice_number", "vendor", "description",
        "amount_original", "currency_original", "amount_ils", "exchange_rate",
        "rate_source", "extraction_confidence", "flagged", "flag_reason",
    }


def test_invoice_to_sidecar_dict_none_dates_stay_none():
    """Regression: the isoformat()-if-truthy guards on received_at/invoice_date
    must not raise or coerce None into a string when a date is genuinely
    missing (e.g. OCR failed to extract invoice_date on a flagged invoice)."""
    invoice = Invoice(
        id="inv-2", group_id="123@g.us", message_id="msg-2", image_hash="hash-2",
        submitted_by="972501@s.whatsapp.net",
        received_at=None,
        invoice_date=None, invoice_number=None, vendor=None,
        description=None, amount_original=None, currency_original=None,
        amount_ils=None, exchange_rate=None, rate_source=None,
        extraction_confidence=None, flagged=True, flag_reason="missing date",
    )
    d = invoice_to_sidecar_dict(invoice)
    assert d["received_at"] is None
    assert d["invoice_date"] is None
    assert d["amount_original"] is None
    assert d["amount_ils"] is None
    assert d["exchange_rate"] is None


def test_invoice_to_sidecar_dict_after_commit_and_session_close():
    """Regression: pipeline.py persists the invoice via `with SessionLocal()
    as db: db.add(invoice); db.commit()`, then calls invoice_to_sidecar_dict(
    invoice) AFTER that block exits (session closed) — this is the real
    production shape, unlike the tests above which use a bare Invoice()
    never added to any session, so they never actually exercise this path.

    SQLAlchemy's default expire_on_commit=True marks every attribute on a
    committed instance as stale; reading an expired attribute normally
    triggers a lazy reload from the session, which raises
    DetachedInstanceError once that session has closed. The fix (a
    db.refresh(invoice) call before the session closes, already the
    pattern used in exec_set_invoice_date/exec_set_invoice_amount) must
    make this safe to read afterward."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    invoice = Invoice(
        id="inv-3", group_id="123@g.us", message_id="msg-3", image_hash="hash-3",
        submitted_by="972501@s.whatsapp.net",
        received_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        invoice_date=date(2026, 7, 14), invoice_number="INV-3", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"), exchange_rate=Decimal("1"), rate_source="boi",
        extraction_confidence=0.9, flagged=False, flag_reason=None,
    )
    with SessionLocal() as db:
        db.add(invoice)
        db.commit()
        db.refresh(invoice)  # the fix: un-expire attributes before the session closes

    # Session is closed now — this must not raise DetachedInstanceError.
    d = invoice_to_sidecar_dict(invoice)
    assert d["invoice_id"] == "inv-3"
    assert d["vendor"] == "Acme"


def _make_jpeg(width: int, height: int, quality: int = 85) -> bytes:
    img = Image.new("RGB", (width, height), color=(120, 40, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_resize_image_returns_input_unchanged_when_already_fitting_jpeg():
    """Performance regression: the bridge already resizes to 1920px/q85
    before sending, so re-decoding and re-encoding an image that's already
    within the target on every single scan is pure wasted wall-clock time
    on the critical path the user is waiting on."""
    already_fitting = _make_jpeg(800, 600)
    assert resize_image(already_fitting) is already_fitting


def test_resize_image_still_resizes_oversized_image():
    oversized = _make_jpeg(3000, 2000)
    result = resize_image(oversized)
    assert result != oversized
    with Image.open(io.BytesIO(result)) as img:
        assert max(img.size) <= 1920


def test_resize_image_converts_non_rgb_mode():
    img = Image.new("L", (400, 300), color=128)  # grayscale, not RGB
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    grayscale_jpeg = buf.getvalue()

    result = resize_image(grayscale_jpeg)
    with Image.open(io.BytesIO(result)) as out:
        assert out.mode == "RGB"


def test_resize_image_converts_non_jpeg_format():
    img = Image.new("RGB", (400, 300), color=(10, 200, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    result = resize_image(png_bytes)
    with Image.open(io.BytesIO(result)) as out:
        assert out.format == "JPEG"
