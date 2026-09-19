"""Tests for app/scheduler.py's _check_bridge_health job.

Covers two distinct failure modes: the bridge being fully unreachable (HTTP
fails), and the bridge being reachable but its /health body reporting it
isn't actually connected to WhatsApp (status != 'ok') — the latter slipped
through undetected in production for 9+ days, since the bridge's Express
server stayed up and kept answering 200 OK the whole time its WhatsApp
socket was stuck in an endless failed-reconnect loop.

State now persists in SystemConfig (not module globals) so it survives an
orchestrator restart mid-outage, and the alert repeats at most once per 24h
until recovery or an explicit dismiss — replacing the old once-until-
recovery email-only behavior, which (even throttled) produced hundreds of
emails to an inbox nobody monitored over a multi-day outage."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import SessionCM
from app.scheduler import (
    _check_bridge_health,
    _save_bridge_alert_state,
    dismiss_bridge_alert,
    get_bridge_alert_state,
)


@pytest.fixture(autouse=True)
def _patch_session_local(db):
    """get_bridge_alert_state/dismiss_bridge_alert open their own SessionLocal()
    (called from the admin API, outside any request-scoped session) — route
    that at the test's in-memory db for every test in this file."""
    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)):
        yield


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
async def test_reachable_and_connected_sends_no_alert_and_clears_state():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="ok")), \
         patch("app.mailer.sms.send_sms") as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()
    assert get_bridge_alert_state() is None


@pytest.mark.asyncio
async def test_first_unhealthy_tick_records_state_but_does_not_alert_yet():
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms") as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()
    state = get_bridge_alert_state()
    assert state is not None
    assert state["last_alert_at"] is None
    assert state["dismissed"] is False


@pytest.mark.asyncio
async def test_reachable_but_not_connected_counts_as_unhealthy():
    """The exact production gap: HTTP succeeds, but the bridge's own socket
    isn't actually connected to WhatsApp — must be tracked the same as an
    outright-unreachable bridge."""
    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="connecting")), \
         patch("app.mailer.sms.send_sms") as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()  # first check — under threshold
    assert get_bridge_alert_state() is not None


@pytest.mark.asyncio
async def test_below_grace_period_does_not_alert(db):
    down_since = datetime.now(timezone.utc) - timedelta(minutes=2)
    _save_bridge_alert_state(db, {"down_since": down_since.isoformat(), "last_alert_at": None, "dismissed": False})

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms") as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_alert_fires_via_sms_once_threshold_crossed(db):
    down_since = datetime.now(timezone.utc) - timedelta(minutes=6)
    _save_bridge_alert_state(db, {"down_since": down_since.isoformat(), "last_alert_at": None, "dismissed": False})

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock) as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_awaited_once()
    args, _ = mock_sms.call_args
    assert "disconnected" in args[1].lower()
    state = get_bridge_alert_state()
    assert state["last_alert_at"] is not None


@pytest.mark.asyncio
async def test_alert_falls_back_to_email_when_sms_not_configured(db):
    down_since = datetime.now(timezone.utc) - timedelta(minutes=6)
    _save_bridge_alert_state(db, {"down_since": down_since.isoformat(), "last_alert_at": None, "dismissed": False})

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock, side_effect=RuntimeError("not configured")), \
         patch("app.mailer.gmail.send_bridge_down_email") as mock_email:
        await _check_bridge_health()

    mock_email.assert_called_once()


@pytest.mark.asyncio
async def test_alert_does_not_repeat_within_24h(db):
    down_since = datetime.now(timezone.utc) - timedelta(hours=2)
    last_alert = datetime.now(timezone.utc) - timedelta(hours=1)
    _save_bridge_alert_state(
        db, {"down_since": down_since.isoformat(), "last_alert_at": last_alert.isoformat(), "dismissed": False}
    )

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock) as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_alert_repeats_after_24h_still_down(db):
    down_since = datetime.now(timezone.utc) - timedelta(hours=30)
    last_alert = datetime.now(timezone.utc) - timedelta(hours=25)
    _save_bridge_alert_state(
        db, {"down_since": down_since.isoformat(), "last_alert_at": last_alert.isoformat(), "dismissed": False}
    )

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock) as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_awaited_once()


@pytest.mark.asyncio
async def test_dismissed_alert_stays_silent_even_past_repeat_interval(db):
    down_since = datetime.now(timezone.utc) - timedelta(hours=30)
    _save_bridge_alert_state(
        db, {"down_since": down_since.isoformat(), "last_alert_at": None, "dismissed": True}
    )

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(raises=ConnectionError("refused"))), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock) as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_clears_state_for_next_outage(db):
    down_since = datetime.now(timezone.utc) - timedelta(hours=1)
    _save_bridge_alert_state(
        db, {"down_since": down_since.isoformat(), "last_alert_at": down_since.isoformat(), "dismissed": False}
    )

    with patch("app.scheduler.httpx.AsyncClient", return_value=_mock_client(status="ok")), \
         patch("app.mailer.sms.send_sms", new_callable=AsyncMock) as mock_sms:
        await _check_bridge_health()

    mock_sms.assert_not_called()
    assert get_bridge_alert_state() is None


def test_dismiss_bridge_alert_marks_existing_state_dismissed(db):
    now = datetime.now(timezone.utc)
    _save_bridge_alert_state(db, {"down_since": now.isoformat(), "last_alert_at": None, "dismissed": False})

    assert dismiss_bridge_alert() is True
    assert get_bridge_alert_state()["dismissed"] is True


def test_dismiss_bridge_alert_returns_false_when_nothing_pending():
    assert dismiss_bridge_alert() is False
