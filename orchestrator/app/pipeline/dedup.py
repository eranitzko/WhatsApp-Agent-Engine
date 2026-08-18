"""Deduplication checks for incoming invoice images and manually-entered invoices.

Layers:
  1. Image hash — same raw bytes already seen (exact duplicate photo).
  2. Perceptual hash — a *visually* near-identical photo already seen, even
     with different bytes (a fresh photo of the same paper receipt, or the
     same photo re-compressed differently on resend). This is what exact
     image_hash can't catch: a user re-sending the same physical receipt as
     a new WhatsApp message essentially always produces different bytes.
  3. Amount + date — same amount and same invoice date already recorded,
     regardless of vendor. Vendor is deliberately NOT part of this check:
     Gemini's OCR reads the same physical vendor name differently across
     separate resends (confirmed directly against production duplicates —
     the same receipt read as four different vendor-name spellings across
     four sends), so requiring an exact vendor match let those duplicates
     through. The odds of two genuinely different vendors producing the
     exact same amount on the exact same day are remote enough to accept
     the occasional false positive in exchange for actually catching the
     far more common case.
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import and_

from app.db.models import Invoice
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Hamming distance (out of 64 bits) below which two images are treated as
# the "same" photo. imagehash's own docs suggest ~10 as a reasonable
# same-image threshold tolerant of recompression/lighting; tune if false
# positives/negatives show up in practice.
PERCEPTUAL_HASH_THRESHOLD = 10


def compute_hash(image_bytes: bytes) -> str:
    """Return the SHA-256 hex digest of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def compute_perceptual_hash(image_bytes: bytes) -> str | None:
    """Return a perceptual hash (hex string) of the image, or None if it
    can't be computed (corrupt/unreadable image — never block ingestion on
    this, it's a secondary check on top of the required exact hash)."""
    try:
        import imagehash
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            return str(imagehash.phash(img))
    except Exception:
        logger.warning("Could not compute perceptual hash", exc_info=True)
        return None


def check_image_hash(group_id: str, image_hash: str) -> Invoice | None:
    """Return an existing Invoice if this exact image was already ingested, else None."""
    with SessionLocal() as db:
        existing = (
            db.query(Invoice)
            .filter(Invoice.group_id == group_id, Invoice.image_hash == image_hash)
            .first()
        )
        if existing:
            db.expunge(existing)
        return existing


def check_perceptual_hash(group_id: str, perceptual_hash: str | None) -> Invoice | None:
    """Return an existing Invoice whose photo is visually near-identical
    (Hamming distance <= PERCEPTUAL_HASH_THRESHOLD), else None. Returns None
    if perceptual_hash is None (couldn't be computed) or no prior invoice in
    this group has one stored."""
    if not perceptual_hash:
        return None

    try:
        import imagehash
        target = imagehash.hex_to_hash(perceptual_hash)
    except Exception:
        return None

    with SessionLocal() as db:
        candidates = (
            db.query(Invoice)
            .filter(Invoice.group_id == group_id, Invoice.perceptual_hash.isnot(None))
            .all()
        )
        best_match = None
        best_distance = None
        for candidate in candidates:
            try:
                distance = target - imagehash.hex_to_hash(candidate.perceptual_hash)
            except Exception:
                continue
            if distance <= PERCEPTUAL_HASH_THRESHOLD and (best_distance is None or distance < best_distance):
                best_match = candidate
                best_distance = distance
        if best_match:
            db.expunge(best_match)
        return best_match


def check_amount_date_duplicate(
    group_id: str,
    amount_original: Decimal | None,
    currency: str | None,
    invoice_date: date | None,
) -> Invoice | None:
    """Return an existing Invoice with the same amount + currency + date,
    else None. Returns None if any field is missing.

    Deliberately does not match on vendor (see module docstring) or
    invoice_number (usually absent on both photographed receipts and
    manually-entered invoices, and vendor+amount+date is already a strong
    enough signal on its own). Used by both the image pipeline and
    save_invoice — the one shared duplicate check for "amount + date
    already recorded," regardless of how the invoice was entered.
    """
    if amount_original is None or not currency or not invoice_date:
        return None

    with SessionLocal() as db:
        existing = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.group_id == group_id,
                    Invoice.amount_original == amount_original,
                    Invoice.currency_original == currency,
                    Invoice.invoice_date == invoice_date,
                )
            )
            .first()
        )
        if existing:
            db.expunge(existing)
        return existing
