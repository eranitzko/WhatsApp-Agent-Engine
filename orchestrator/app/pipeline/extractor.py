"""Gemini vision extraction for invoice images.

Sends full-resolution image bytes to Gemini 1.5 Pro and returns structured JSON:
  {
    "invoice_date": "YYYY-MM-DD" | null,
    "invoice_number": str | null,
    "vendor": str | null,
    "description": str | null,          # short AI-generated description
    "amount_original": float | null,
    "currency_original": "ILS" | "USD" | ... | null,
    "confidence": float                  # 0.0–1.0
  }

Confidence is estimated from the presence of key fields and Gemini's own language
(e.g. "unclear", "cannot read"). Low-quality images are flagged by the pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import google.generativeai as genai

from app.config import settings
from app.utils.retry import retry
from app.utils.date_formats import parse_format_string, try_fmt

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.gemini_api_key)

_EXTRACTION_PROMPT = """\
You are an invoice OCR assistant. Extract structured data from the invoice image provided.

Return ONLY a JSON object with these keys (use null for anything you cannot read):
{
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "vendor": "string or null",
  "description": "one short sentence describing what was purchased or the service provided",
  "amount_original": number or null,
  "currency_original": "ISO 4217 code (e.g. ILS, USD, EUR) or null",
  "confidence": float between 0.0 and 1.0 reflecting how legible and complete the invoice is
}

Rules:
- invoice_date must be output in YYYY-MM-DD format.
- IMPORTANT: These invoices are from Israel. Israeli dates are written DD/MM/YYYY (day first, then month).
  For example, "12/03/2024" means March 12 2024, not December 3. Convert accordingly to YYYY-MM-DD.
- Some Israeli receipts print the year as only 2 digits, i.e. DD/MM/YY (e.g. "23/07/26"). The date is
  STILL day first: the FIRST group is the day, the MIDDLE group is the month, and the LAST group is the
  2-digit year (expand YY to 20YY). Do not mistake the day for the year just because both are 2-digit
  numbers. For example, "23/07/26" means July 23 2026 (day=23, month=07, year=2026) — the day is NOT the
  year, and the 2-digit year is NOT the day.
- currency_original must be a 3-letter ISO 4217 code (e.g. ILS, USD, EUR, GBP).
- amount_original is the total amount due (the grand total including VAT if shown).
- description should be concise (under 10 words). State what was purchased or the service — do NOT start with "purchase of", "payment for", "רכישת", or similar preambles. Just name the item or service directly. Write in the same language as the invoice — do NOT translate to English.
- vendor must be written exactly as it appears on the invoice — preserve the original language and spelling.
- confidence: 1.0 = all fields clearly legible; 0.0 = image unreadable.
- Output only the JSON object. No explanation, no markdown fences.
"""

# Fields that contribute to the confidence estimate (used for cross-validation)
_KEY_FIELDS = ("invoice_date", "invoice_number", "vendor", "amount_original", "currency_original")


def _build_prompt(custom_instructions: str | None) -> str:
    """Append admin-configured group hints (GroupRegistry.custom_instructions —
    the same free text already fed to the conversational agent's system
    prompt) to the base extraction prompt. Keeps Gemini's OCR in sync with
    whatever an admin has told the group's chat agent about this group's
    invoices (date formats, expected currency, vendor spelling quirks, etc.)
    instead of only the agent knowing about it.
    """
    if not custom_instructions or not custom_instructions.strip():
        return _EXTRACTION_PROMPT
    return (
        f"{_EXTRACTION_PROMPT}\n"
        "Group-specific hints from the admin (apply these in addition to the "
        "rules above; they do not override the required JSON schema or field types):\n"
        f"{custom_instructions.strip()}\n"
    )


def _try_extra_date_formats(raw_date: str) -> date | None:
    """Try admin-configured extra date formats from SystemConfig against raw_date."""
    try:
        from app.db.session import SessionLocal
        from app.db.models import SystemConfig
        with SessionLocal() as db:
            row = db.get(SystemConfig, "extra_date_formats")
            if not row or not row.value.strip():
                return None
            formats_str = row.value  # capture before session closes
        for fmt_str in formats_str.split(","):
            fmt_str = fmt_str.strip()
            if not fmt_str:
                continue
            fmt = parse_format_string(fmt_str)
            if fmt:
                result = try_fmt(raw_date, fmt)
                if result:
                    return result
    except Exception:
        pass
    return None


def _validate_and_normalise(raw: dict) -> dict:
    """Normalise Gemini output and ensure all expected keys exist."""
    out: dict = {}

    # invoice_date: coerce to date string YYYY-MM-DD
    raw_date = raw.get("invoice_date")
    if raw_date and isinstance(raw_date, str):
        # Try YYYY-MM-DD (or YYYY/MM/DD, YYYY.MM.DD) first
        match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw_date)
        if match:
            y, m, d = match.groups()
            try:
                parsed = date(int(y), int(m), int(d))
                out["invoice_date"] = parsed.isoformat()
            except ValueError:
                out["invoice_date"] = None
        else:
            # Fallback: DD/MM/YYYY or DD.MM.YYYY (Israeli/European format)
            match = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", raw_date)
            if match:
                d, m, y = match.groups()
                try:
                    parsed = date(int(y), int(m), int(d))
                    out["invoice_date"] = parsed.isoformat()
                except ValueError:
                    out["invoice_date"] = None
            else:
                extra = _try_extra_date_formats(raw_date)
                out["invoice_date"] = extra.isoformat() if extra else None
    else:
        out["invoice_date"] = None

    out["invoice_number"] = raw.get("invoice_number") or None
    out["vendor"]         = raw.get("vendor") or None
    out["description"]    = raw.get("description") or None

    # amount_original: see app/utils/invoice_amount.py for what counts as
    # valid (nonzero; negative is fine — a refund/return/credit receipt).
    from app.utils.invoice_amount import validate_invoice_amount
    validated = validate_invoice_amount(raw.get("amount_original"))
    out["amount_original"] = float(validated.amount) if validated.amount is not None else None

    # currency_original: 3-letter uppercase ISO 4217
    cur = raw.get("currency_original")
    if cur and isinstance(cur, str) and re.fullmatch(r"[A-Z]{3}", cur.strip().upper()):
        out["currency_original"] = cur.strip().upper()
    else:
        out["currency_original"] = None

    # confidence: Gemini's own score, clamped 0–1; cross-validate against field presence
    try:
        gemini_conf = float(raw.get("confidence", 0.5))
        gemini_conf = max(0.0, min(1.0, gemini_conf))
    except (TypeError, ValueError):
        gemini_conf = 0.5

    present = sum(1 for k in _KEY_FIELDS if out.get(k) is not None)
    field_conf = present / len(_KEY_FIELDS)
    # Weighted average: trust Gemini 60%, field presence 40%
    out["confidence"] = round(0.6 * gemini_conf + 0.4 * field_conf, 3)

    return out


def _call_gemini_sync(image_bytes: bytes, mime_type: str, custom_instructions: str | None = None) -> str:
    """Synchronous Gemini API call — must be run in a thread pool to avoid blocking the event loop."""
    from google.api_core import retry as google_retry
    # Disable the SDK's own retry logic — we handle retries ourselves,
    # and we deliberately do NOT retry 429 (rate limit) errors.
    no_retry = google_retry.Retry(predicate=lambda _: False)

    model = genai.GenerativeModel(settings.gemini_model)
    image_part = {"mime_type": mime_type, "data": image_bytes}
    response = model.generate_content(
        [_build_prompt(custom_instructions), image_part],
        request_options={"retry": no_retry},
    )
    return response.text.strip()


async def _call_gemini(image_bytes: bytes, mime_type: str, custom_instructions: str | None = None) -> str:
    """Run the blocking Gemini call in a thread pool so the event loop stays free."""
    import asyncio
    return await asyncio.to_thread(_call_gemini_sync, image_bytes, mime_type, custom_instructions)


async def extract_invoice(image_bytes: bytes, mime_type: str = "image/jpeg", custom_instructions: str | None = None) -> dict:
    """Send image bytes to Gemini and return structured extraction result.

    custom_instructions: optional group-specific hints (GroupRegistry.custom_instructions)
    appended to the base prompt — the same text already used in the group's
    conversational agent, kept in sync so an admin only has to say a thing once.

    Returns a dict with keys: invoice_date, invoice_number, vendor, description,
    amount_original, currency_original, confidence.
    On failure, returns {"error": str, "confidence": 0.0}.
    """
    try:
        raw_text = await retry(_call_gemini, image_bytes, mime_type, custom_instructions, retries=3, base_delay=2.0)

        # Strip markdown code fences if Gemini wrapped the JSON
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        raw = json.loads(raw_text)
        return _validate_and_normalise(raw)

    except json.JSONDecodeError as exc:
        logger.warning("Gemini returned non-JSON output: %s", exc)
        return {"error": f"Could not parse Gemini response: {exc}", "confidence": 0.0}
    except Exception as exc:
        logger.exception("Gemini extraction failed: %s", exc)
        return {"error": str(exc), "confidence": 0.0}
