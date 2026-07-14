"""Phone-number normalization and sender-identity resolution.

normalize_phone() — all phone numbers stored in the DB must go through it at
every write site and every comparison/lookup site.  The function is
intentionally narrow: it strips non-digit characters and validates the result
as a 7–18 digit numeric string.  It does NOT attempt to map WhatsApp
LID-format opaque numbers (e.g. '8650248708313') to human E.164 numbers —
LIDs are stored as-is and used as opaque keys.  The guarantee is consistency:
the same raw value always produces the same stored value.

resolve_sender_phone() — the canonical way to get the resolved sender phone
for a tool-call/webhook context (ctx["resolved_phone"] with a raw-JID-split
fallback).  Use it at every call site that needs "who sent this message,
resolved" instead of re-deriving a raw split inline — that duplication is
exactly what caused several live LID-attribution bugs before this function
existed.
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


def resolve_sender_phone(ctx: dict) -> str:
    """Return the resolved canonical phone for the current tool-call context.

    Prefers ctx["resolved_phone"] (the LID-safe phone agent_runner already
    computed via resolve_inbound) over re-deriving one from the raw
    sender JID — that raw fallback is only correct for phone-format senders;
    in shared groups WhatsApp sends an opaque LID instead, and using it
    directly misattributes ledger entries / lookups to the wrong identity.
    Falls back to the raw sender split only when resolved_phone is absent.
    """
    if resolved := ctx.get("resolved_phone"):
        return resolved
    sender = ctx.get("sender", "")
    return sender.split("@")[0].split(":")[0] if sender else ""
