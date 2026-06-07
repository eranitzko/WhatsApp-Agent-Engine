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


# ── API: GET /people email + allowlist CRUD + auto-sync ──────────────────────

from app.db.models import AdminNumbers, UserProfile, UserAccount, EmailAllowlist as _EA


def _person_app(db):
    """Return (TestClient, patcher-context) with SessionLocal patched to db."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch as _p
    from app.admin.api import router as _router
    from app.admin.auth import require_auth as _auth
    import app.admin.api as _api_mod

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    app = FastAPI()
    app.include_router(_router, prefix="/admin/api")
    app.dependency_overrides[_auth] = lambda: None
    ctx = _p.object(_api_mod, "SessionLocal", return_value=_CM())
    ctx.start()
    client = TestClient(app)
    return client, ctx


def test_get_people_includes_email(db):
    db.add(UserProfile(phone="972501234567", display_name="Alice", email="alice@example.com"))
    db.add(AdminNumbers(phone_number="972501234567"))
    db.commit()
    client, ctx = _person_app(db)
    try:
        resp = client.get("/admin/api/people")
    finally:
        ctx.stop()
    assert resp.status_code == 200
    people = resp.json()
    assert len(people) == 1
    assert people[0]["email"] == "alice@example.com"


def test_allowlist_crud(db):
    client, ctx = _person_app(db)
    try:
        # GET empty
        resp = client.get("/admin/api/settings/email-allowlist")
        assert resp.status_code == 200
        assert resp.json() == []

        # POST add
        resp = client.post("/admin/api/settings/email-allowlist",
                           json={"email": "boss@company.com", "display_name": "Boss"})
        assert resp.status_code == 200

        # GET shows entry
        resp = client.get("/admin/api/settings/email-allowlist")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["email"] == "boss@company.com"
        assert data[0]["display_name"] == "Boss"

        # POST duplicate → 409
        resp = client.post("/admin/api/settings/email-allowlist",
                           json={"email": "boss@company.com"})
        assert resp.status_code == 409

        # DELETE
        resp = client.delete("/admin/api/settings/email-allowlist/boss@company.com")
        assert resp.status_code == 200
        assert db.query(_EA).count() == 0

        # DELETE non-existent → 404
        resp = client.delete("/admin/api/settings/email-allowlist/ghost@company.com")
        assert resp.status_code == 404
    finally:
        ctx.stop()


def test_patch_person_email_syncs_to_allowlist(db):
    db.add(UserProfile(phone="972501234567", display_name="Alice"))
    db.add(AdminNumbers(phone_number="972501234567"))
    db.commit()
    client, ctx = _person_app(db)
    try:
        # Set email → appears in allowlist
        resp = client.patch("/admin/api/people/972501234567",
                            json={"email": "alice@example.com"})
        assert resp.status_code == 200
        row = db.query(_EA).filter_by(email="alice@example.com").first()
        assert row is not None
        assert row.display_name == "Alice"

        # Clear email → removed from allowlist
        resp = client.patch("/admin/api/people/972501234567",
                            json={"email": ""})
        assert resp.status_code == 200
        row = db.query(_EA).filter_by(email="alice@example.com").first()
        assert row is None
    finally:
        ctx.stop()
