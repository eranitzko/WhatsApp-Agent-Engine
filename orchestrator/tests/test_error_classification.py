"""Tests for app/utils/error_classification.py.

Constructs realistic instances of each exception type (matching what the
real SDKs actually raise, not just message strings) so this stays accurate
if a library's exception shape changes."""

import anthropic
import httpx
import pytest
import sqlalchemy.exc

from app.utils.error_classification import classify_error


def _anthropic_status_error(cls, status_code: int, body: dict) -> Exception:
    resp = httpx.Response(status_code, request=httpx.Request("POST", "https://api.anthropic.com"), json=body)
    return cls(f"Error code: {status_code} - {body}", response=resp, body=body)


def test_classify_anthropic_credit_balance_too_low():
    body = {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.",
    }}
    exc = _anthropic_status_error(anthropic.BadRequestError, 400, body)
    msg = classify_error(exc)
    assert "credit" in msg.lower() or "funds" in msg.lower() or "balance" in msg.lower()
    assert "admin" in msg.lower()


def test_classify_anthropic_other_bad_request_is_generic_ai_message():
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens too large"}}
    exc = _anthropic_status_error(anthropic.BadRequestError, 400, body)
    msg = classify_error(exc)
    assert "AI" in msg or "ai" in msg.lower()
    assert "credit" not in msg.lower()


def test_classify_anthropic_rate_limit():
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}}
    exc = _anthropic_status_error(anthropic.RateLimitError, 429, body)
    msg = classify_error(exc)
    assert "rate" in msg.lower() or "try again" in msg.lower()


def test_classify_anthropic_auth_error():
    body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    exc = _anthropic_status_error(anthropic.AuthenticationError, 401, body)
    msg = classify_error(exc)
    assert "key" in msg.lower() or "auth" in msg.lower()


def test_classify_anthropic_server_error():
    body = {"type": "error", "error": {"type": "api_error", "message": "internal server error"}}
    exc = _anthropic_status_error(anthropic.InternalServerError, 500, body)
    msg = classify_error(exc)
    assert "anthropic" in msg.lower() or "ai service" in msg.lower()


def test_classify_anthropic_connection_error():
    req = httpx.Request("POST", "https://api.anthropic.com")
    exc = anthropic.APIConnectionError(message="Connection error.", request=req)
    msg = classify_error(exc)
    assert "network" in msg.lower() or "reach" in msg.lower()


def test_classify_sqlalchemy_error():
    exc = sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("db down"))
    msg = classify_error(exc)
    assert "database" in msg.lower()


def test_classify_httpx_connect_error():
    exc = httpx.ConnectError("Connection refused")
    msg = classify_error(exc)
    assert "network" in msg.lower()


def test_classify_unknown_exception_falls_back_to_generic():
    msg = classify_error(ValueError("something totally unexpected"))
    assert "something went wrong" in msg.lower()
