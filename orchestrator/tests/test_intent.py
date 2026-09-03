"""Tests for app/agent/intent.py — the AI fallback for confirmation replies
and outbound-message localization.

Root cause this exists for: every yes/no confirmation flow in this app
(cross-group payment confirmations, multi-party confirmations, single-action
stage_action, sys-admin group registration) only ever recognized a fixed,
small word list (app/agent/reply_words.py). A completely natural Hebrew
reply like "לאשר" ("to approve") isn't in that list, so it silently failed
to register as an approval — even though the system has an LLM available on
every single turn. This module replaces "grow the word list forever" with
"ask the model that's already running everything," used only as a fallback
after the free, zero-latency exact-match check has already failed.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.intent import classify_confirmation_reply, compose_localized_message


def _mock_client_returning(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_classify_confirmation_reply_recognizes_natural_hebrew_approval():
    """Regression: "לאשר" (the natural Hebrew infinitive for "approve") is
    not in reply_words.CONFIRM_WORDS — this is the actual fallback that
    should now catch it."""
    client = _mock_client_returning("CONFIRM")
    decision = await classify_confirmation_reply(
        client, "claude-sonnet-4-6",
        pending_description="Eden says they paid you ₪570.00 on 2026-09-02.",
        reply_text="לאשר",
    )
    assert decision == "confirm"


@pytest.mark.asyncio
async def test_classify_confirmation_reply_recognizes_rejection():
    client = _mock_client_returning("REJECT")
    decision = await classify_confirmation_reply(
        client, "claude-sonnet-4-6",
        pending_description="Eden says they paid you ₪570.00.",
        reply_text="זה לא נכון",
    )
    assert decision == "reject"


@pytest.mark.asyncio
async def test_classify_confirmation_reply_unclear_for_unrelated_text():
    client = _mock_client_returning("UNCLEAR")
    decision = await classify_confirmation_reply(
        client, "claude-sonnet-4-6",
        pending_description="Eden says they paid you ₪570.00.",
        reply_text="מה שלומך?",
    )
    assert decision == "unclear"


@pytest.mark.asyncio
async def test_classify_confirmation_reply_empty_text_is_unclear_without_api_call():
    client = _mock_client_returning("CONFIRM")
    decision = await classify_confirmation_reply(
        client, "claude-sonnet-4-6",
        pending_description="Eden says they paid you ₪570.00.",
        reply_text="   ",
    )
    assert decision == "unclear"
    client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_classify_confirmation_reply_api_failure_is_unclear_not_raised():
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("network down"))
    decision = await classify_confirmation_reply(
        client, "claude-sonnet-4-6",
        pending_description="Eden says they paid you ₪570.00.",
        reply_text="לאשר",
    )
    assert decision == "unclear"


@pytest.mark.asyncio
async def test_compose_localized_message_returns_model_output():
    client = _mock_client_returning("עדן אומרת ששילמה לך 570 שקל. לאשר?")
    result = await compose_localized_message(
        client, "claude-sonnet-4-6",
        english_text="Eden says they paid you 570 ILS. Confirm?",
        language_sample="עדן החזירה 570",
    )
    assert result == "עדן אומרת ששילמה לך 570 שקל. לאשר?"


@pytest.mark.asyncio
async def test_compose_localized_message_falls_back_to_english_on_failure():
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("network down"))
    result = await compose_localized_message(
        client, "claude-sonnet-4-6",
        english_text="Eden says they paid you 570 ILS. Confirm?",
        language_sample="עדן החזירה 570",
    )
    assert result == "Eden says they paid you 570 ILS. Confirm?"
