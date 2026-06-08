"""Tests for admin panel REST API endpoints."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.admin.api import router as api_router
from app.db.models import Blueprint, GroupRegistry, AdminNumbers


# -- helpers -----------------------------------------------------------------

class _SessionCM:
    """Context manager that returns a new session from the given factory."""

    def __init__(self, factory):
        self._factory = factory
        self._sess = None

    def __enter__(self):
        self._sess = self._factory()
        return self._sess

    def __exit__(self, *a):
        if self._sess:
            self._sess.close()


def _seed(db):
    db.add(Blueprint(id="fa", display_name="Family Accounting",
                     system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="111@g.us", blueprint_id="fa"))
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()


def _make_app(db, *, override_auth=True):
    """Build a FastAPI app with auth optionally bypassed."""
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    if override_auth:
        from app.admin.auth import require_auth
        app.dependency_overrides[require_auth] = lambda: None
    return app


# -- /admin/api/groups -------------------------------------------------------

@pytest.mark.asyncio
async def test_list_groups(db):
    _seed(db)
    db.close()  # flush so the route's new session can see the data

    Session = _get_session_factory(db)

    from app.admin import api as admin_api

    async def _mock_bridge_groups():
        return {"111@g.us": {"name": "Test Group", "participants": []}}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _mock_bridge_groups):
        app = _make_app(db)
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["group_jid"] == "111@g.us"
        assert data[0]["group_name"] == "Test Group"
        assert data[0]["blueprint_name"] == "Family Accounting"


@pytest.mark.asyncio
async def test_list_groups_bridge_fallback(db):
    """When bridge is unreachable, group_name falls back to group_jid."""
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    from app.admin import api as admin_api
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)

    async def _empty_bridge_groups():
        return {}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _empty_bridge_groups):
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["group_name"] == "111@g.us"  # fallback to JID


@pytest.mark.asyncio
async def test_register_group(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "222@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    verify = Session()
    row = verify.query(GroupRegistry).filter_by(group_jid="222@g.us").first()
    assert row is not None
    assert row.blueprint_id == "fa"
    verify.close()


@pytest.mark.asyncio
async def test_delete_group(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.delete("/admin/api/groups/111%40g.us")
        assert resp.status_code == 200

    verify = Session()
    assert verify.query(GroupRegistry).filter_by(group_jid="111@g.us").first() is None
    verify.close()


# -- /admin/api/admins -------------------------------------------------------

def test_list_admins(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.get("/admin/api/admins")
        assert resp.status_code == 200
        phones = [a["phone_number"] for a in resp.json()]
        assert "972500000001" in phones


def test_add_admin(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/admins", json={"phone_number": "972500000099"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    verify = Session()
    assert verify.query(AdminNumbers).filter_by(phone_number="972500000099").first() is not None
    verify.close()


def test_delete_admin(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.delete("/admin/api/admins/972500000001")
        assert resp.status_code == 200

    verify = Session()
    assert verify.query(AdminNumbers).filter_by(phone_number="972500000001").first() is None
    verify.close()


# -- /admin/api/blueprints ---------------------------------------------------

def test_list_blueprints(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app)
        resp = client.get("/admin/api/blueprints")
        assert resp.status_code == 200
        names = [b["display_name"] for b in resp.json()]
        assert "Family Accounting" in names


# -- auth required -----------------------------------------------------------

def test_endpoints_require_auth(db):
    """Without overriding require_auth, all endpoints should return 401."""
    app = _make_app(db, override_auth=False)

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/admin/api/groups").status_code == 401
        assert client.get("/admin/api/admins").status_code == 401
        assert client.get("/admin/api/blueprints").status_code == 401


def test_register_group_duplicate_returns_409(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "111@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 409


def test_add_admin_duplicate_returns_409(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: _SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/api/admins",
                           json={"phone_number": "972500000001"})
        assert resp.status_code == 409


# -- internal helper ---------------------------------------------------------

def _get_session_factory(db):
    """Return a sessionmaker bound to the same engine as ``db``."""
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=db.get_bind())
