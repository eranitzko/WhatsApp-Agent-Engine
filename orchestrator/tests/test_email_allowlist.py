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
