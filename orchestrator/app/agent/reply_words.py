"""Canonical yes/no reply-word recognition, shared across every
confirmation flow in the app: single-action stage_action
(app/agent/confirmation.py), multi-party confirmations
(app/agent/multi_confirmation.py), cross-group confirmations and sys-admin
group-registration approval (both in app/main.py and
app/accounting/group_registration.py).

Previously each flow re-implemented its own word list independently and
they had already drifted — e.g. one flow didn't recognize "אישור"/"ביטול"
that a sibling flow (50 lines away in the same file) did, silently
rejecting a valid approval reply. This module holds the union of every
word any flow has ever recognized.
"""
from __future__ import annotations

CONFIRM_WORDS = {"yes", "y", "ok", "confirm", "approve", "כן", "אישור", "יאללה"}
CANCEL_WORDS = {"no", "n", "cancel", "abort", "reject", "לא", "ביטול"}


def is_affirmative(text: str) -> bool:
    return text.strip().lower() in CONFIRM_WORDS


def is_negative(text: str) -> bool:
    return text.strip().lower() in CANCEL_WORDS
