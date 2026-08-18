"""Tests for app/scheduler.py's _check_bridge_health job.

Covers two distinct failure modes: the bridge being fully unreachable (HTTP
fails), and the bridge being reachable but its /health body reporting it
isn't actually connected to WhatsApp (status != 'ok') — the latter is what
slipped through undetected in production for 9+ days, since the bridge's
Express server stayed up and kept answering 200 OK the whole time its
WhatsApp socket was stuck in an endless failed-reconnect loop. Module-level
state (_bridge_down_since, _bridge_alert_sent) is reset before each test."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.scheduler as scheduler_module
from app.scheduler import _check_bridge_health


@pytest.fixture(autouse=True)
def _reset_bridge_health_state():
    scheduler_module._bridge_down_since = None
    scheduler_module._bridge_alert_sent = False
    yield
    scheduler_module._bridge_down_since = None
    scheduler_module._bridge_alert_sent = False


def _mock_client(*, raises: Exception | None = None, status: str = "ok"):
    mock_client = AsyncMock()
    if raises:
        mock_client.get = AsyncMock(side_effect=raises)
    else:
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": status}
        mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_reachable_and_connected_sends_no_email_and_clears_state():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="ok")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_http_unreachable_first_failure_tracks_start_time_but_does_not_email_yet():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is not None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_reachable_but_not_connected_counts_as_unhealthy():
    """The exact production gap: HTTP succeeds, but the bridge's own socket
    isn't actually connected to WhatsApp — this must be tracked the same as
    an outright-unreachable bridge, not silently treated as healthy."""
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="connecting")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()  # first check — under threshold
    assert scheduler_module._bridge_down_since is not None


@pytest.mark.asyncio
async def test_alert_fires_once_threshold_crossed_for_unreachable_bridge():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=6)

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_called_once()
    assert scheduler_module._bridge_alert_sent is True


@pytest.mark.asyncio
async def test_alert_fires_once_threshold_crossed_for_reachable_but_disconnected_bridge():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=6)

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="connecting")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_called_once()
    args, _ = mock_email.call_args
    assert "connecting" in args[1] or "not connected" in args[1]
    assert scheduler_module._bridge_alert_sent is True


@pytest.mark.asyncio
async def test_alert_does_not_resend_while_still_down():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=10)
    scheduler_module._bridge_alert_sent = True

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_after_alert_resets_state_for_next_outage():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=10)
    scheduler_module._bridge_alert_sent = True

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="ok")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_recovery_from_connecting_status_resets_state():
    """A bridge that was stuck reporting 'connecting' must be recognized as
    healthy again once it reports 'ok', not just once it becomes reachable."""
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=10)
    scheduler_module._bridge_alert_sent = True

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="ok")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_below_threshold_failure_does_not_email():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=2)

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_alert_sent is False
