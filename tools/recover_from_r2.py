"""Recovery script: re-extract invoice metadata from R2 images that have no DB entry.

Scans R2 for .jpg files whose key is NOT referenced by any invoice row,
re-runs Gemini OCR on each, and inserts the result into the DB.

Usage (run inside orchestrator container):
    python /tmp/recover_from_r2.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GROUP_JID = "120363426326481102@g.us"


async def main():
    from app.config import settings
    from app.db.models import GroupRegistry, Invoice
    from app.db.session import SessionLocal
    from app.pipeline.converter import convert_to_ils
    from app.pipeline.dedup import compute_hash
    from app.pipeline.extractor import extract_invoice
    from app.pipeline.storage import upload_metadata

    with SessionLocal() as db:
        registry_entry = db.get(GroupRegistry, GROUP_JID)
        custom_instructions = (registry_entry.custom_instructions or "") if registry_entry else ""

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )

    # Get all .jpg keys in R2 for this group
    result = s3.list_objects_v2(Bucket=settings.r2_bucket, Prefix=f"invoices/{GROUP_JID}/")
    all_keys = {o["Key"] for o in result.get("Contents", []) if o["Key"].endswith(".jpg")}
    logger.info("R2 has %d images for this group", len(all_keys))

    # Get all r2_keys already in the DB
    with SessionLocal() as db:
        existing_keys = {r[0] for r in db.query(Invoice.r2_key).filter(Invoice.group_id == GROUP_JID).all() if r[0]}
        existing_hashes = {r[0] for r in db.query(Invoice.image_hash).filter(Invoice.group_id == GROUP_JID).all() if r[0]}

    missing_keys = all_keys - existing_keys
    logger.info("Missing from DB: %d images — recovering now", len(missing_keys))

    recovered = 0
    failed = 0

    for r2_key in sorted(missing_keys):
        logger.info("Processing %s", r2_key)
        try:
            # Download image from R2
            resp = s3.get_object(Bucket=settings.r2_bucket, Key=r2_key)
            image_bytes = resp["Body"].read()

            # Skip if image hash already in DB (different r2_key but same image)
            image_hash = compute_hash(image_bytes)
            if image_hash in existing_hashes:
                logger.info("  Skipping — duplicate image hash")
                continue

            # Re-run Gemini OCR
            extraction = await extract_invoice(image_bytes, "image/jpeg", custom_instructions)
            if extraction.get("error"):
                logger.warning("  Extraction failed: %s", extraction["error"])
                failed += 1
                continue

            invoice_date_str = extraction.get("invoice_date")
            invoice_date = None
            if invoice_date_str:
                try:
                    invoice_date = date.fromisoformat(invoice_date_str)
                    # Sanity check — reject dates before 2020
                    if invoice_date.year < 2020:
                        logger.warning("  Suspicious date %s — setting to None", invoice_date)
                        invoice_date = None
                        invoice_date_str = None
                except ValueError:
                    invoice_date = None

            invoice_number = extraction.get("invoice_number")
            vendor        = extraction.get("vendor")
            description   = extraction.get("description")
            confidence    = float(extraction.get("confidence", 0.5))

            amount_raw = extraction.get("amount_original")
            try:
                amount_original = Decimal(str(amount_raw)) if amount_raw is not None else None
            except InvalidOperation:
                amount_original = None
            currency_original = extraction.get("currency_original")

            # Currency conversion
            amount_ils = exchange_rate = rate_source = rate_date_val = None
            if amount_original and currency_original:
                conv = await convert_to_ils(amount_original, currency_original, invoice_date)
                if not conv.error:
                    amount_ils    = conv.amount_ils
                    exchange_rate = conv.exchange_rate
                    rate_source   = conv.rate_source
                    rate_date_val = conv.rate_date

            flagged = confidence < 0.6
            flag_reason = f"Low confidence ({confidence:.0%}) — recovered from R2" if flagged else None

            invoice_id = str(uuid.uuid4())
            invoice = Invoice(
                id=invoice_id,
                group_id=GROUP_JID,
                message_id=f"recovered:{r2_key}",  # synthetic message_id
                image_hash=image_hash,
                r2_key=r2_key,
                received_at=datetime.now(timezone.utc),
                invoice_date=invoice_date,
                invoice_number=invoice_number,
                vendor=vendor,
                description=description,
                amount_original=amount_original,
                currency_original=currency_original,
                amount_ils=amount_ils,
                exchange_rate=exchange_rate,
                rate_source=rate_source,
                rate_date=rate_date_val,
                extraction_confidence=confidence,
                flagged=flagged,
                flag_reason=flag_reason,
                submitted_by="recovered",
            )
            with SessionLocal() as db:
                db.add(invoice)
                db.commit()

            # Upload JSON sidecar now that we have metadata
            await upload_metadata(r2_key, {
                "invoice_id": invoice_id,
                "group_id": GROUP_JID,
                "message_id": f"recovered:{r2_key}",
                "image_hash": image_hash,
                "submitted_by": "recovered",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "invoice_date": invoice_date_str,
                "invoice_number": invoice_number,
                "vendor": vendor,
                "description": description,
                "amount_original": float(amount_original) if amount_original else None,
                "currency_original": currency_original,
                "amount_ils": float(amount_ils) if amount_ils else None,
                "exchange_rate": float(exchange_rate) if exchange_rate else None,
                "rate_source": rate_source,
                "extraction_confidence": confidence,
                "flagged": flagged,
                "flag_reason": flag_reason,
            })

            existing_hashes.add(image_hash)
            logger.info("  ✅ Recovered: %s | %s | %s %s", vendor, invoice_date_str, amount_original, currency_original)
            recovered += 1

        except Exception as exc:
            logger.exception("  Failed to recover %s: %s", r2_key, exc)
            failed += 1

    logger.info("\nDone. Recovered: %d  Failed: %d", recovered, failed)


if __name__ == "__main__":
    asyncio.run(main())
