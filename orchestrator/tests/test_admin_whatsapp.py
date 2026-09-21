"""Tests for the admin panel's WhatsApp connection endpoints
(/admin/api/whatsapp/*) — self-service reconnect QR + alert status/dismiss,
added so an operator never has to depend on a notification (email or SMS)
arriving in time to get a fresh, still-valid QR code."""

import base64
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.api import router as api_router
from app.scheduler import _save_bridge_alert_state
from tests.conftest import SessionCM


def _make_app():
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    from app.admin.auth import require_auth
    app.dependency_overrides[require_auth] = lambda: None
    return app


def _mock_bridge_client(*, raises: Exception | None = None, status_code: int = 200, json_body: dict | None = None):
    mock_client = AsyncMock()
    if raises:
        mock_client.get = AsyncMock(side_effect=raises)
    else:
        resp = MagicMock(status_code=status_code)
        resp.json.return_value = json_body or {}
        mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture(autouse=True)
def _reset_sms_credit_cache():
    from app.admin import api as admin_api
    admin_api._sms_credit_cache["value"] = None
    admin_api._sms_credit_cache["checked_at"] = None
    yield
    admin_api._sms_credit_cache["value"] = None
    admin_api._sms_credit_cache["checked_at"] = None


def test_status_reports_ok_with_no_alert():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"status": "ok"})), \
         patch("app.scheduler.get_bridge_alert_state", return_value=None), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, side_effect=RuntimeError("not configured")):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["alert"] is None


def test_status_reports_connecting_with_active_alert():
    alert = {"down_since": "2026-09-19T12:00:00+00:00", "last_alert_at": None, "dismissed": False}
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"status": "connecting"})), \
         patch("app.scheduler.get_bridge_alert_state", return_value=alert), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, side_effect=RuntimeError("not configured")):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "connecting"
    assert body["alert"] == alert


def test_status_reports_unreachable_when_bridge_unreachable():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(raises=ConnectionError("refused"))), \
         patch("app.scheduler.get_bridge_alert_state", return_value=None), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, side_effect=RuntimeError("not configured")):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "unreachable"


# -- SMS credit balance (cached — the panel polls /whatsapp/status every 5s,
#    far more often than the balance could plausibly change) -----------------

def test_status_includes_sms_credit_balance_when_configured():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"status": "ok"})), \
         patch("app.scheduler.get_bridge_alert_state", return_value=None), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, return_value=850):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/status")

    assert resp.json()["sms_credits"] == 850


def test_status_sms_credits_is_none_when_vibrate_not_configured():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"status": "ok"})), \
         patch("app.scheduler.get_bridge_alert_state", return_value=None), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, side_effect=RuntimeError("not configured")):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/status")

    assert resp.json()["sms_credits"] is None


def test_status_sms_credits_reuses_cache_within_ttl():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"status": "ok"})), \
         patch("app.scheduler.get_bridge_alert_state", return_value=None), \
         patch("app.mailer.sms.get_credit_balance", new_callable=AsyncMock, return_value=850) as mock_balance:
        client = TestClient(_make_app())
        r1 = client.get("/admin/api/whatsapp/status")
        r2 = client.get("/admin/api/whatsapp/status")

    assert r1.json()["sms_credits"] == 850
    assert r2.json()["sms_credits"] == 850
    mock_balance.assert_awaited_once()  # second call served from cache


def test_qr_returns_png_base64_when_bridge_has_a_pending_code():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(json_body={"qr": "1@fakepairingstring"})):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/qr")

    assert resp.status_code == 200
    png_bytes = base64.b64decode(resp.json()["png_base64"])
    assert png_bytes.startswith(b"\x89PNG")  # PNG magic bytes


def test_qr_returns_404_when_bridge_has_no_pending_code():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(status_code=404, json_body={"error": "none"})):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/qr")

    assert resp.status_code == 404


def test_qr_returns_502_when_bridge_unreachable():
    with patch("app.admin.api.httpx.AsyncClient", return_value=_mock_bridge_client(raises=ConnectionError("refused"))):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/qr")

    assert resp.status_code == 502


def test_dismiss_alert_marks_active_outage_dismissed(db):
    now = datetime.now(timezone.utc)
    _save_bridge_alert_state(db, {"down_since": now.isoformat(), "last_alert_at": None, "dismissed": False})

    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)):
        client = TestClient(_make_app())
        resp = client.post("/admin/api/whatsapp/dismiss-alert")

    assert resp.status_code == 200
    assert resp.json() == {"dismissed": True}


def test_dismiss_alert_returns_false_when_nothing_pending(db):
    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)):
        client = TestClient(_make_app())
        resp = client.post("/admin/api/whatsapp/dismiss-alert")

    assert resp.status_code == 200
    assert resp.json() == {"dismissed": False}


def test_outage_log_returns_history(db):
    from app.db.models import BridgeOutageLog
    db.add(BridgeOutageLog(
        down_since=datetime.now(timezone.utc) - timedelta(hours=2),
        recovered_at=datetime.now(timezone.utc) - timedelta(hours=1),
        reason="bridge unreachable: refused",
    ))
    db.commit()

    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)):
        client = TestClient(_make_app())
        resp = client.get("/admin/api/whatsapp/outage-log")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["reason"] == "bridge unreachable: refused"
    assert body[0]["recovered_at"] is not None


def test_whatsapp_endpoints_require_auth():
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    client = TestClient(app)

    assert client.get("/admin/api/whatsapp/status").status_code == 401
    assert client.get("/admin/api/whatsapp/qr").status_code == 401
    assert client.post("/admin/api/whatsapp/dismiss-alert").status_code == 401
    assert client.get("/admin/api/whatsapp/outage-log").status_code == 401
