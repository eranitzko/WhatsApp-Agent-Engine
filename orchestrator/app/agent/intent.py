"""AI-based fallback for confirmation replies and outbound-message localization.

Every yes/no confirmation flow in this app (cross-group payment
confirmations, multi-party confirmations, single-action stage_action,
sys-admin group registration) recognizes replies via a fixed word list
(app/agent/reply_words.py). A completely natural reply like "לאשר" (Hebrew
for "to approve") isn't in that list, so it silently failed to register as
an approval — despite an LLM being available on every turn. Growing the word
list forever doesn't fix that; asking the model does.

These functions are a fallback, used only AFTER the free, zero-latency
exact-match check in reply_words.py has already failed to classify a reply —
they never replace or race that fast path.
"""
from __future__ import annotations

import logging
from typing import Literal

import anthropic

logger = logging.getLogger(__name__)

Decision = Literal["confirm", "reject", "unclear"]


async def classify_confirmation_reply(
    client: anthropic.AsyncAnthropic,
    model: str,
    *,
    pending_description: str,
    reply_text: str,
) -> Decision:
    """Classify a free-form reply to a pending yes/no confirmation.

    Any failure (empty text, API error, unparseable response) resolves to
    "unclear" rather than raising — a missed confirmation should fall
    through to a normal, informative reply, never crash the message handler.
    """
    if not reply_text.strip():
        return "unclear"
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=8,
            temperature=0,
            system=(
                "You classify a WhatsApp reply to a pending yes/no confirmation. "
                "Reply with exactly one word: CONFIRM, REJECT, or UNCLEAR. "
                "The user may reply in any language, any phrasing, including "
                "single words, slang, or emoji. Only classify CONFIRM or REJECT "
                "when the intent is unambiguous — otherwise UNCLEAR."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Pending confirmation: {pending_description}\n"
                    f"User's reply: {reply_text!r}\n"
                    "Classification:"
                ),
            }],
        )
        text = next((b.text for b in resp.content if hasattr(b, "text")), "").strip().upper()
    except Exception:
        logger.exception("classify_confirmation_reply failed — treating as unclear")
        return "unclear"

    if "CONFIRM" in text:
        return "confirm"
    if "REJECT" in text:
        return "reject"
    return "unclear"


async def compose_localized_message(
    client: anthropic.AsyncAnthropic,
    model: str,
    *,
    english_text: str,
    language_sample: str,
) -> str:
    """Rewrite a system-generated notification in the sample's language.

    Several outbound WhatsApp messages (cross-group payment confirmations,
    sys-admin registration prompts) are composed outside of any agent turn,
    so the "respond in the language of the user's message" instruction in
    the blueprint system prompts never applies to them — they were hardcoded
    English regardless of the conversation's actual language. Falls back to
    the original English text on any failure.
    """
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=200,
            temperature=0,
            system=(
                "Rewrite the given WhatsApp message in the same language as the "
                "sample text. Keep the meaning, names, and numbers exactly as "
                "given. Reply with ONLY the rewritten message, no preamble."
            ),
            messages=[{
                "role": "user",
                "content": f"Sample (for language only): {language_sample!r}\n\nMessage to rewrite:\n{english_text}",
            }],
        )
        text = next((b.text for b in resp.content if hasattr(b, "text")), "").strip()
        return text or english_text
    except Exception:
        logger.exception("compose_localized_message failed — falling back to English")
        return english_text
