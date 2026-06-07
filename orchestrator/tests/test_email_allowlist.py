"""Tests for email allowlist: ORM, API endpoints, and send_email _is_allowed."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.api import router as api_router
from app.admin.auth import require_auth
from app.db.models import EmailAllowlist


def _make_app(db):
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None
    return app


# ── ORM ──────────────────────────────────────────────────────────────────────

def test_email_allowlist_model_created(db):
    db.add(EmailAllowlist(email="test@example.com", display_name="Test User"))
    db.commit()
    row = db.query(EmailAllowlist).filter_by(email="test@example.com").first()
    assert row is not None
    assert row.display_name == "Test User"


# ── _is_allowed ──────────────────────────────────────────────────────────────

def test_is_allowed_empty_table_permits_any(db):
    """Empty allowlist → any address is allowed."""
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("anyone@anywhere.com", db=db) is True


def test_is_allowed_blocks_unlisted(db):
    from app.db.models import EmailAllowlist
    db.add(EmailAllowlist(email="boss@company.com", display_name="Boss"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("stranger@evil.com", db=db) is False


def test_is_allowed_permits_listed(db):
    from app.db.models import EmailAllowlist
    db.add(EmailAllowlist(email="boss@company.com", display_name="Boss"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("boss@company.com", db=db) is True


def test_is_allowed_case_insensitive(db):
    from app.db.models import EmailAllowlist
    db.add(EmailAllowlist(email="boss@company.com"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("BOSS@COMPANY.COM", db=db) is True
