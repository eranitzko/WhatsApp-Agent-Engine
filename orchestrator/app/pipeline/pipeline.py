"""Invoice ingestion pipeline.

Orchestrates the full flow for an incoming image event:
  1. Compute SHA-256 hash → duplicate check (image hash)
  2. Gemini OCR → structured extraction
  3. Duplicate check (invoice number + vendor)
  4. Confidence check → flag if below threshold
  5. In-memory resize → R2 upload
  6. Currency conversion
  7. Persist to DB
  8. Return result dict for the agent to acknowledge

Call process_image_event(event) from main.py for the "image" event type.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from app.config import settings
from app.db.models import Invoice
from app.db.session import SessionLocal
from app.pipeline.converter import convert_to_ils
from app.pipeline.dedup import check_image_hash, check_invoice_key, compute_hash
from app.pipeline.extractor import extract_invoice
from app.pipeline.storage import upload_image, upload_metadata

logger = logging.getLogger(__name__)


async def process_image_event(event: dict) -> dict:
    """Run the full invoice ingestion pipeline for an incoming image event.

    Expected event keys:
      - jid: WhatsApp group JID
      - sender: sender's JID
      - messageId: WhatsApp message ID
      - imageBytes: raw image bytes (bytes)
      - mimeType: MIME type string, default "image/jpeg"

    Returns a result dict suitable for passing to agent.handle_image_event:
      - On success: {invoice_id, vendor, amount_original, currency_original,
                     amount_ils, invoice_date, invoice_number, description,
                     confidence, flagged, flag_reason, duplicate, ...}
      - On error:   {error: str}
    """
    jid        = event.get("jid", "")
    sender     = event.get("sender", "")
    message_id = event.get("messageId", "")
    mime_type  = event.get("mimeType", "image/jpeg")

    # Bridge sends base64-encoded image bytes
    image_b64: str = event.get("imageBase64", "")
    if not image_b64:
        return {"error": "No image data provided."}

    try:
        image_bytes: bytes = base64.b64decode(image_b64)
    except Exception as exc:
        return {"error": f"Failed to decode image: {exc}"}

    # ── 1. Hash ────────────────────────────────────────────────────────────────
    image_hash = compute_hash(image_bytes)
    logger.info("Processing image hash=%s from %s", image_hash[:12], sender)

    # ── 2. Image-hash dedup ────────────────────────────────────────────────────
    existing_by_hash = check_image_hash(jid, image_hash)
    if existing_by_hash:
        logger.info("Duplicate image hash detected: %s", existing_by_hash.id)
        return {
            "duplicate": True,
            "duplicate_reason": "identical_image",
            "existing_invoice_id": existing_by_hash.id,
            "existing_vendor": existing_by_hash.vendor,
            "existing_date": str(existing_by_hash.invoice_date) if existing_by_hash.invoice_date else None,
            "existing_amount": (
                f"{existing_by_hash.amount_original} {existing_by_hash.currency_original}"
                if existing_by_hash.amount_original else None
            ),
        }

    # ── 3. Gemini OCR ──────────────────────────────────────────────────────────
    extraction = await extract_invoice(image_bytes, mime_type)

    if extraction.get("error"):
        # Still persist a placeholder so the image isn't lost
        logger.warning("Extraction failed: %s", extraction["error"])
        return {"error": extraction["error"]}

    invoice_date_str: str | None = extraction.get("invoice_date")
    invoice_date: date | None = None
    if invoice_date_str:
        try:
            invoice_date = date.fromisoformat(invoice_date_str)
        except ValueError:
            invoice_date = None

    invoice_number = extraction.get("invoice_number")
    vendor         = extraction.get("vendor")
    description    = extraction.get("description")
    confidence     = float(extraction.get("confidence", 0.5))

    amount_raw = extraction.get("amount_original")
    try:
        amount_original = Decimal(str(amount_raw)) if amount_raw is not None else None
    except InvalidOperation:
        amount_original = None

    currency_original = extraction.get("currency_original")

    # ── 4. Invoice-key dedup ──────────────────────────────────────────────────
    existing_by_key = check_invoice_key(jid, invoice_number, vendor, invoice_date)
    if existing_by_key:
        logger.info("Duplicate invoice key detected: %s", existing_by_key.id)
        return {
            "duplicate": True,
            "duplicate_reason": "same_invoice_number_and_vendor",
            "existing_invoice_id": existing_by_key.id,
            "existing_vendor": existing_by_key.vendor,
            "existing_date": str(existing_by_key.invoice_date) if existing_by_key.invoice_date else None,
            "existing_amount": (
                f"{existing_by_key.amount_original} {existing_by_key.currency_original}"
                if existing_by_key.amount_original else None
            ),
            # Include extraction so agent can compare
            "extracted_vendor": vendor,
            "extracted_date": invoice_date_str,
            "extracted_amount": f"{amount_original} {currency_original}" if amount_original else None,
        }

    # ── 5. Confidence check + flagging ─────────────────────────────────────────
    flagged = False
    flag_reason: str | None = None

    if confidence < settings.confidence_flag_threshold:
        flagged = True
        flag_reason = f"Low extraction confidence ({confidence:.0%}). Please review manually."
        logger.info("Invoice flagged: low confidence %.2f", confidence)

    # ── 6. R2 upload ──────────────────────────────────────────────────────────
    r2_key: str | None = None
    try:
        r2_key = await upload_image(image_bytes, jid, message_id)
    except RuntimeError as exc:
        logger.error("R2 upload failed: %s — invoice saved without image", exc)
        if not flagged:
            flagged = True
            flag_reason = f"Image upload failed: {exc}"

    # ── 7. Currency conversion ─────────────────────────────────────────────────
    amount_ils    = None
    exchange_rate = None
    rate_source   = None
    rate_date_val = None

    if amount_original is not None and currency_original:
        conversion = await convert_to_ils(amount_original, currency_original, invoice_date)
        if conversion.error:
            logger.warning("Currency conversion failed: %s", conversion.error)
            if not flagged:
                flagged = True
                flag_reason = conversion.error
        else:
            amount_ils    = conversion.amount_ils
            exchange_rate = conversion.exchange_rate
            rate_source   = conversion.rate_source
            rate_date_val = conversion.rate_date

    # ── 8. Persist to DB ──────────────────────────────────────────────────────
    # Guard against duplicate message_id (bridge retry or replayed message)
    with SessionLocal() as db:
        if db.query(Invoice).filter(Invoice.message_id == message_id).first():
            logger.info("Duplicate message_id %s — skipping insert", message_id)
            return {"duplicate": True, "duplicate_reason": "duplicate_message_id"}

    invoice_id = str(uuid.uuid4())
    invoice = Invoice(
        id                    = invoice_id,
        group_id              = jid,
        message_id            = message_id,
        image_hash            = image_hash,
        r2_key                = r2_key,
        received_at           = datetime.now(timezone.utc),
        invoice_date          = invoice_date,
        invoice_number        = invoice_number,
        vendor                = vendor,
        description           = description,
        amount_original       = amount_original,
        currency_original     = currency_original,
        amount_ils            = amount_ils,
        exchange_rate         = exchange_rate,
        rate_source           = rate_source,
        rate_date             = rate_date_val,
        extraction_confidence = confidence,
        flagged               = flagged,
        flag_reason           = flag_reason,
        submitted_by          = sender,
    )

    with SessionLocal() as db:
        db.add(invoice)
        db.commit()

    logger.info(
        "Invoice saved: id=%s vendor=%s amount=%s %s ils=%s flagged=%s",
        invoice_id, vendor, amount_original, currency_original, amount_ils, flagged,
    )

    # ── 9. R2 metadata sidecar ────────────────────────────────────────────────
    # Upload extracted metadata as a JSON sidecar alongside the image so R2
    # is the source of truth and the DB can be rebuilt from R2 if needed.
    if r2_key:
        try:
            await upload_metadata(r2_key, {
                "invoice_id":          invoice_id,
                "group_id":            jid,
                "message_id":          message_id,
                "image_hash":          image_hash,
                "submitted_by":        sender,
                "received_at":         datetime.now(timezone.utc).isoformat(),
                "invoice_date":        invoice_date_str,
                "invoice_number":      invoice_number,
                "vendor":              vendor,
                "description":         description,
                "amount_original":     float(amount_original) if amount_original else None,
                "currency_original":   currency_original,
                "amount_ils":          float(amount_ils) if amount_ils else None,
                "exchange_rate":       float(exchange_rate) if exchange_rate else None,
                "rate_source":         rate_source,
                "extraction_confidence": confidence,
                "flagged":             flagged,
                "flag_reason":         flag_reason,
            })
        except RuntimeError:
            logger.warning("Metadata sidecar upload failed for invoice %s — data still in DB", invoice_id)

    return {
        "invoice_id":       invoice_id,
        "vendor":           vendor,
        "invoice_number":   invoice_number,
        "invoice_date":     invoice_date_str,
        "description":      description,
        "amount_original":  float(amount_original) if amount_original else None,
        "currency_original": currency_original,
        "amount_ils":       float(amount_ils) if amount_ils else None,
        "exchange_rate":    float(exchange_rate) if exchange_rate else None,
        "rate_source":      rate_source,
        "confidence":       confidence,
        "flagged":          flagged,
        "flag_reason":      flag_reason,
        "r2_key":           r2_key,
        "duplicate":        False,
    }
