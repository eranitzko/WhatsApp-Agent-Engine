"""Tests for app/mailer/sms.py — Twilio SMS delivery for bridge-down alerts."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mailer.sms import send_sms


@pytest.mark.asyncio
async def test_send_sms_raises_when_not_configured():
    with patch("app.mailer.sms.settings") as mock_settings:
        mock_settings.twilio_account_sid = ""
        mock_settings.twilio_auth_token = ""
        mock_settings.twilio_from_number = ""
        with pytest.raises(RuntimeError, match="Twilio"):
            await send_sms("972500000000", "test")


@pytest.mark.asyncio
async def test_send_sms_posts_to_twilio_with_basic_auth():
    resp = MagicMock(status_code=201, text="")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.twilio_account_sid = "ACtest"
        mock_settings.twilio_auth_token = "secret"
        mock_settings.twilio_from_number = "+15551234567"
        await send_sms("972500000000", "Bridge is down")

    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.call_args
    assert "ACtest" in args[0]
    assert kwargs["data"]["To"] == "972500000000"
    assert kwargs["data"]["From"] == "+15551234567"
    assert kwargs["data"]["Body"] == "Bridge is down"
    assert kwargs["auth"] == ("ACtest", "secret")


@pytest.mark.asyncio
async def test_send_sms_raises_on_twilio_error_response():
    resp = MagicMock(status_code=400, text="Invalid 'To' Phone Number")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.twilio_account_sid = "ACtest"
        mock_settings.twilio_auth_token = "secret"
        mock_settings.twilio_from_number = "+15551234567"
        with pytest.raises(RuntimeError, match="Invalid"):
            await send_sms("972500000000", "Bridge is down")
