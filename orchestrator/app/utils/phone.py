"""Phone-number normalization.

All phone numbers stored in the DB must go through normalize_phone() at every
write site and every comparison/lookup site.  The function is intentionally
narrow: it strips non-digit characters and validates the result as a 7–18 digit
numeric string.  It does NOT attempt to map WhatsApp LID-format opaque numbers
(e.g. '8650248708313') to human E.164 numbers — LIDs are stored as-is and used
as opaque keys.  The guarantee is consistency: the same raw value always produces
the same stored value.
"""

from __future__ import annotations

import re

_PHONE_RE = re.compile(r'^\d{7,18}$')


def normalize_phone(raw: str | None) -> str | None:
    """Strip leading +, spaces, and non-digit characters; validate format.

    Returns None when raw is None or empty.
    Raises ValueError for strings that are non-empty but invalid after stripping.
    """
    if not raw:
        return None
    phone = re.sub(r'[^\d]', '', raw.strip())
    if not _PHONE_RE.match(phone):
        raise ValueError(f"Invalid phone number: {raw!r} (normalized: {phone!r})")
    return phone
