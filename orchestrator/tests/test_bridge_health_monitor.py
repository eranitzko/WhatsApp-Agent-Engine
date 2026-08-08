"""Tests for app/scheduler.py's _check_bridge_health job.

The bridge can't report its own absence if the whole container is down, so
the orchestrator polls it and emails once (not per-check) after it's been
unreachable past a threshold. Module-level state (_bridge_down_since,
_bridge_alert_sent) is reset before each test for isolation."""

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


def _mock_client(*, raises: Exception | None = None):
    mock_client = AsyncMock()
    if raises:
        mock_client.get = AsyncMock(side_effect=raises)
    else:
        mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_reachable_bridge_sends_no_email_and_clears_state():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client()), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_first_failure_tracks_start_time_but_does_not_email_yet():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_not_called()
    assert scheduler_module._bridge_down_since is not None
    assert scheduler_module._bridge_alert_sent is False


@pytest.mark.asyncio
async def test_alert_fires_once_threshold_crossed():
    scheduler_module._bridge_down_since = datetime.now(timezone.utc) - timedelta(minutes=6)

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_called_once()
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

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client()), \
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
