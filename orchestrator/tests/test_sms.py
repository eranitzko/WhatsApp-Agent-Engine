"""Tests for app/mailer/sms.py — Vibrate (vibrate.co.il) SMS delivery for
bridge-down alerts. Israeli SMS gateway: one-time prepaid credit packages
(no monthly fee, messages never expire), REST API documented at
https://www.vibrate.co.il/vibrate-api-skill.md."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mailer.sms import send_sms, get_delivery_status, get_credit_balance


@pytest.mark.asyncio
async def test_send_sms_raises_when_not_configured():
    with patch("app.mailer.sms.settings") as mock_settings:
        mock_settings.vibrate_access_token = ""
        mock_settings.vibrate_sender = ""
        with pytest.raises(RuntimeError, match="Vibrate"):
            await send_sms("972500000000", "test")


@pytest.mark.asyncio
async def test_send_sms_posts_to_vibrate_with_access_token():
    resp = MagicMock(status_code=202, text="")
    resp.json.return_value = {"success": True, "data": {"runId": "abc-123"}}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        mock_settings.vibrate_sender = "MyBot"
        await send_sms("972500000000", "Bridge is down")

    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://api.vibrate.co.il/v1/sms/send"
    assert kwargs["headers"]["Authorization"] == "vb_test_token"
    assert kwargs["json"]["recipients"] == ["972500000000"]
    assert kwargs["json"]["message"] == "Bridge is down"
    assert kwargs["json"]["sender"] == "MyBot"


@pytest.mark.asyncio
async def test_send_sms_returns_the_run_id():
    """The runId is needed to later poll delivery status — dropping it on
    the floor makes "did the alert actually arrive" unanswerable."""
    resp = MagicMock(status_code=202, text="")
    resp.json.return_value = {"success": True, "data": {"runId": "run-xyz-789"}}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        mock_settings.vibrate_sender = "MyBot"
        run_id = await send_sms("972500000000", "Bridge is down")

    assert run_id == "run-xyz-789"


@pytest.mark.asyncio
async def test_send_sms_raises_on_vibrate_error_response():
    resp = MagicMock(status_code=400, text='{"code":"SMS_INVALID_SENDER"}')
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        mock_settings.vibrate_sender = "MyBot"
        with pytest.raises(RuntimeError, match="SMS_INVALID_SENDER"):
            await send_sms("972500000000", "Bridge is down")


@pytest.mark.asyncio
async def test_send_sms_rejects_unexpected_status_codes_other_than_202():
    """Vibrate's send endpoints always return 202 (queued, not delivered) on
    success — anything else (even a 2xx like 200) is unexpected and must not
    be silently treated as success by a lenient status_code < 300 check."""
    resp = MagicMock(status_code=200, text="unexpected")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        mock_settings.vibrate_sender = "MyBot"
        with pytest.raises(RuntimeError):
            await send_sms("972500000000", "Bridge is down")


# -- Delivery confirmation -----------------------------------------------------

@pytest.mark.asyncio
async def test_get_delivery_status_returns_parsed_summary():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "success": True,
        "data": {
            "runId": "run-xyz-789",
            "allDelivered": True,
            "summary": {"total": 1, "delivered": 1, "pending": 0},
        },
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        status = await get_delivery_status("run-xyz-789")

    mock_client.get.assert_awaited_once()
    args, kwargs = mock_client.get.call_args
    assert args[0] == "https://api.vibrate.co.il/v1/sms/run/run-xyz-789/delivery-status"
    assert status["allDelivered"] is True
    assert status["summary"]["delivered"] == 1


@pytest.mark.asyncio
async def test_get_delivery_status_raises_on_error():
    resp = MagicMock(status_code=404, text='{"code":"RESOURCE_NOT_FOUND"}')
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        with pytest.raises(RuntimeError, match="RESOURCE_NOT_FOUND"):
            await get_delivery_status("run-xyz-789")


# -- Credit balance -------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_credit_balance_returns_sms_amount():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "success": True,
        "data": {"email": "e@example.com", "name": "Eran", "status": "active", "smsAmount": 850},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.mailer.sms.settings") as mock_settings, \
         patch("app.mailer.sms.httpx.AsyncClient", return_value=mock_client):
        mock_settings.vibrate_access_token = "vb_test_token"
        balance = await get_credit_balance()

    assert balance == 850


@pytest.mark.asyncio
async def test_get_credit_balance_raises_when_not_configured():
    with patch("app.mailer.sms.settings") as mock_settings:
        mock_settings.vibrate_access_token = ""
        with pytest.raises(RuntimeError, match="Vibrate"):
            await get_credit_balance()
