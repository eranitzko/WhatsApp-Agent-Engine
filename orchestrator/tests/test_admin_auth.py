"""Tests for admin panel auth — password verification and JWT lifecycle."""

import time
import pytest
from unittest.mock import patch
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


def test_verify_token_tampered_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
    # Tamper with the token
    tampered = token[:-4] + "XXXX"
    assert verify_token(tampered) is False


def test_verify_token_empty_string_returns_false():
    assert verify_token("") is False
