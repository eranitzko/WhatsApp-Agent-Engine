"""Deduplication checks for incoming invoice images and manually-entered invoices.

Three-layer dedup:
  1. Image hash — same raw bytes already seen (exact duplicate photo)
  2. Invoice key — same invoice number + vendor already recorded this month
     (same invoice re-photographed or sent twice with edits)
  3. Manual-entry match — same vendor + amount + currency + date already
     recorded (save_invoice, unlike the image pipeline, had no dedup at all;
     the agent would sometimes call it redundantly right after an image was
     already successfully processed, e.g. misreading the pipeline's own
     "New invoice received" notification as a request to save it again)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import and_

from app.db.models import Invoice
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def compute_hash(image_bytes: bytes) -> str:
    """Return the SHA-256 hex digest of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


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


def check_similar_manual_invoice(
    group_id: str,
    vendor: str | None,
    amount_original: Decimal | None,
    currency: str | None,
    invoice_date: date | None,
) -> Invoice | None:
    """Return an existing Invoice with the same vendor + amount + currency +
    date, else None. Returns None if any field is missing.

    check_invoice_key requires invoice_number, which manually-entered
    invoices (save_invoice) usually don't have — so it never fires for
    them. This is the equivalent check for that path."""
    if not vendor or amount_original is None or not currency or not invoice_date:
        return None

    with SessionLocal() as db:
        existing = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.group_id == group_id,
                    Invoice.vendor == vendor,
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


def check_invoice_key(
    group_id: str,
    invoice_number: str | None,
    vendor: str | None,
    invoice_date: date | None,
) -> Invoice | None:
    """Return an existing Invoice if the same invoice number + vendor exists for the
    same month/year, else None. Returns None if either field is missing."""
    if not invoice_number or not vendor or not invoice_date:
        return None

    month_start = invoice_date.replace(day=1)
    # End of month: first day of next month
    if invoice_date.month == 12:
        month_end = invoice_date.replace(year=invoice_date.year + 1, month=1, day=1)
    else:
        month_end = invoice_date.replace(month=invoice_date.month + 1, day=1)

    with SessionLocal() as db:
        existing = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.group_id == group_id,
                    Invoice.invoice_number == invoice_number,
                    Invoice.vendor == vendor,
                    Invoice.invoice_date >= month_start,
                    Invoice.invoice_date < month_end,
                )
            )
            .first()
        )
        if existing:
            db.expunge(existing)
        return existing
