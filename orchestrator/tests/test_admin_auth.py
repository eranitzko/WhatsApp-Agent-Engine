"""Tests for admin panel auth — password verification and JWT lifecycle."""

import time
import pytest
from unittest.mock import patch
from fastapi import Depends
from app.admin.auth import create_token, verify_token, AdminAuthError


def test_create_token_returns_string():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
    assert isinstance(token, str)
    assert len(token) > 20


def test_create_token_wrong_password_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        with pytest.raises(AdminAuthError):
            create_token("wrongpassword")


def test_create_token_empty_password_configured_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = ""
        with pytest.raises(AdminAuthError):
            create_token("anything")


def test_verify_token_valid():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
        assert verify_token(token) is True


def test_verify_token_tampered_returns_false():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
    # Tamper with the token
    tampered = token[:-4] + "XXXX"
    assert verify_token(tampered) is False


def test_verify_token_empty_string_returns_false():
    assert verify_token("") is False


def test_require_auth_valid_token_passes():
    """A valid token should not raise."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.admin.auth import require_auth

    app = FastAPI()

    @app.get("/protected")
    def protected(_=Depends(require_auth)):
        return {"ok": True}

    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "testpass"
        token = create_token("testpass")
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_require_auth_no_token_returns_401():
    """Missing Authorization header should return 401."""
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app.admin.auth import require_auth

    app = FastAPI()

    @app.get("/protected")
    def protected(_=Depends(require_auth)):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_require_auth_invalid_token_returns_401():
    """Invalid token should return 401."""
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app.admin.auth import require_auth

    app = FastAPI()

    @app.get("/protected")
    def protected(_=Depends(require_auth)):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": "Bearer invalidtoken"})
    assert resp.status_code == 401
