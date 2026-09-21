"""Tests for app/tools/send_sms_tool.py — the agent-invokable wrapper around
app.mailer.sms.send_sms. Admin only, since SMS reaches a phone directly
over the cellular network (outside WhatsApp) and costs real prepaid credit,
unlike every other message this bot sends."""
from unittest.mock import AsyncMock, patch

import pytest

from app.tools.send_sms_tool import _exec_send_sms, get_send_sms_tools


@pytest.mark.asyncio
async def test_send_sms_tool_rejected_if_not_admin():
    result = await _exec_send_sms({"to": "972500000000", "message": "hi"}, is_admin=False)
    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_send_sms_tool_requires_to():
    result = await _exec_send_sms({"message": "hi"}, is_admin=True)
    assert "to" in result.lower()


@pytest.mark.asyncio
async def test_send_sms_tool_requires_message():
    result = await _exec_send_sms({"to": "972500000000"}, is_admin=True)
    assert "message" in result.lower()


@pytest.mark.asyncio
async def test_send_sms_tool_sends_and_confirms():
    with patch("app.tools.send_sms_tool.send_sms", new_callable=AsyncMock, return_value="run-1") as mock_send:
        result = await _exec_send_sms(
            {"to": "972500000000", "message": "Your order shipped"}, is_admin=True
        )

    mock_send.assert_awaited_once_with("972500000000", "Your order shipped")
    assert "972500000000" in result


@pytest.mark.asyncio
async def test_send_sms_tool_surfaces_provider_failure():
    with patch(
        "app.tools.send_sms_tool.send_sms",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Vibrate SMS send failed (400): SMS_INVALID_SENDER"),
    ):
        result = await _exec_send_sms({"to": "972500000000", "message": "hi"}, is_admin=True)

    assert "failed" in result.lower()
    assert "SMS_INVALID_SENDER" in result


def test_get_send_sms_tools_returns_tool():
    tools = get_send_sms_tools()
    assert "send_sms" in tools
    assert "schema" in tools["send_sms"]
    assert "executor" in tools["send_sms"]
    assert tools["send_sms"]["schema"]["access"] == "admin"
