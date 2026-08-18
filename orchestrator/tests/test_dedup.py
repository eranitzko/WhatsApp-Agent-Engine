"""Tests for app/pipeline/dedup.py.

Covers the two dedup checks added/changed to catch what the previous
vendor-based checks missed: real production duplicates where the same
physical receipt was re-sent as a fresh photo (different bytes each time,
so exact image_hash misses it, and Gemini OCR read the vendor name
differently on each pass, so vendor-based matching missed it too)."""

import io
from datetime import date
from decimal import Decimal

from PIL import Image, ImageDraw

from app.pipeline.dedup import (
    check_amount_date_duplicate,
    check_perceptual_hash,
    compute_perceptual_hash,
)
from tests.conftest import SessionCM, make_invoice


def _test_image_bytes(seed: int, fmt: str = "JPEG", quality: int = 85) -> bytes:
    """A simple textured (non-solid-color) image, varied by seed, so
    perceptual hashing has actual structure to compare instead of the
    degenerate all-zero hash a solid color produces."""
    img = Image.new("RGB", (200, 200), color="white")
    draw = ImageDraw.Draw(img)
    for i in range(0, 200, 20):
        draw.line([(i, 0), (i + seed, 200)], fill=(i % 256, (i * 2) % 256, seed % 256), width=3)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


def test_compute_perceptual_hash_returns_hex_string():
    h = compute_perceptual_hash(_test_image_bytes(seed=1))
    assert h is not None
    assert len(h) == 16
    int(h, 16)  # valid hex


def test_compute_perceptual_hash_returns_none_for_garbage_bytes():
    assert compute_perceptual_hash(b"not an image") is None


def test_perceptual_hash_recompressed_same_image_is_near_identical():
    """The core scenario: the same photo re-sent/re-compressed produces
    different exact bytes but a small perceptual-hash distance."""
    import imagehash

    original = _test_image_bytes(seed=1, quality=85)
    recompressed = _test_image_bytes(seed=1, quality=60)  # same content, different compression

    h1 = imagehash.hex_to_hash(compute_perceptual_hash(original))
    h2 = imagehash.hex_to_hash(compute_perceptual_hash(recompressed))
    assert (h1 - h2) <= 10


def test_perceptual_hash_different_image_is_far():
    import imagehash

    h1 = imagehash.hex_to_hash(compute_perceptual_hash(_test_image_bytes(seed=1)))
    h2 = imagehash.hex_to_hash(compute_perceptual_hash(_test_image_bytes(seed=97)))
    assert (h1 - h2) > 10


def test_check_perceptual_hash_matches_near_identical_stored_hash(db, monkeypatch):
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    phash_original = compute_perceptual_hash(_test_image_bytes(seed=1, quality=85))
    phash_resend = compute_perceptual_hash(_test_image_bytes(seed=1, quality=60))

    make_invoice(db, group_id="123@g.us", perceptual_hash=phash_original)

    match = check_perceptual_hash("123@g.us", phash_resend)
    assert match is not None


def test_check_perceptual_hash_no_match_for_different_image(db, monkeypatch):
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    phash_original = compute_perceptual_hash(_test_image_bytes(seed=1))
    phash_different = compute_perceptual_hash(_test_image_bytes(seed=97))

    make_invoice(db, group_id="123@g.us", perceptual_hash=phash_original)

    assert check_perceptual_hash("123@g.us", phash_different) is None


def test_check_perceptual_hash_none_input_returns_none(db, monkeypatch):
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    assert check_perceptual_hash("123@g.us", None) is None


def test_check_amount_date_duplicate_ignores_vendor(db, monkeypatch):
    """The behavior change this whole redesign was for: two OCR reads of
    the same physical receipt with different vendor-name spellings must
    still be recognized as the same invoice."""
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    make_invoice(
        db, group_id="123@g.us", vendor="כוכולית עין חרוד איחוד",
        amount_original=Decimal("211.56"), currency_original="ILS",
        invoice_date=date(2026, 8, 3),
    )

    match = check_amount_date_duplicate("123@g.us", Decimal("211.56"), "ILS", date(2026, 8, 3))
    assert match is not None


def test_check_amount_date_duplicate_different_date_no_match(db, monkeypatch):
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    make_invoice(
        db, group_id="123@g.us", amount_original=Decimal("30"),
        currency_original="ILS", invoice_date=date(2026, 8, 1),
    )

    match = check_amount_date_duplicate("123@g.us", Decimal("30"), "ILS", date(2026, 8, 8))
    assert match is None


def test_check_amount_date_duplicate_missing_field_returns_none(db, monkeypatch):
    monkeypatch.setattr("app.pipeline.dedup.SessionLocal", lambda: SessionCM(db))
    assert check_amount_date_duplicate("123@g.us", None, "ILS", date(2026, 8, 8)) is None
    assert check_amount_date_duplicate("123@g.us", Decimal("30"), "ILS", None) is None
