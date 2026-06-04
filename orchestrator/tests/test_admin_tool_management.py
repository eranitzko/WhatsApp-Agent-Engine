"""Tests for admin tool management API endpoints."""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.api import router as api_router
from app.admin.auth import require_auth
from app.db.models import Blueprint, SystemConfig
from app.tool_registry import ToolRegistry
from app import registry_ref


def _make_app(db):
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None
    return app


def _make_registry(*tool_names_and_categories: tuple[str, str]) -> ToolRegistry:
    """Build a ToolRegistry with lightweight fake tools for testing."""
    reg = ToolRegistry()
    tools = {}
    for name, category in tool_names_and_categories:
        tools[name] = {
            "schema": {
                "name": name,
                "description": f"Test tool {name}",
                "category": category,
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            "executor": None,
        }
    reg.register(tools)
    return reg


def _seed(db):
    db.add(Blueprint(
        id="family_accounting",
        display_name="Family Accounting",
        system_prompt="p",
        tools_enabled=json.dumps(["tool_a", "tool_b"]),
    ))
    db.add(Blueprint(
        id="invoice_curator",
        display_name="Invoice Curator",
        system_prompt="p",
        tools_enabled=json.dumps(["tool_b", "tool_c"]),
    ))
    db.commit()


def test_all_tools_have_category_field():
    from app.tools.automation_tools import get_automation_tools
    from app.tools.accounting_tools import get_accounting_tools
    from app.tools.invoice_tools import get_invoice_tools

    for name, entry in get_automation_tools().items():
        assert "category" in entry["schema"], f"automation tool {name} missing category"
        assert entry["schema"]["category"] == "automation"

    for name, entry in get_accounting_tools().items():
        assert "category" in entry["schema"], f"accounting tool {name} missing category"
        assert entry["schema"]["category"] == "accounting"

    for name, entry in get_invoice_tools().items():
        assert "category" in entry["schema"], f"invoice tool {name} missing category"
        assert entry["schema"]["category"] == "invoices"


@pytest.mark.asyncio
async def test_agent_runner_filters_globally_disabled_tools(db):
    """Globally disabled tools are removed from allowed_tools before inference."""
    import anthropic
    from unittest.mock import MagicMock, AsyncMock
    from app.agent_runner import AgentRunner
    from app.tool_registry import ToolRegistry
    from app.db.models import Blueprint, SystemConfig

    # Seed a blueprint and a disabled tool entry
    db.add(Blueprint(
        id="test_bp", display_name="Test", system_prompt="p",
        tools_enabled=json.dumps(["tool_a", "tool_b"]),
        model="claude-haiku-4-5", max_tool_turns=1,
        context_window=4, context_idle_reset_minutes=60,
    ))
    db.add(SystemConfig(key="disabled_tools", value=json.dumps(["tool_b"])))
    db.commit()

    captured_tools: list = []

    async def fake_create(**kwargs):
        captured_tools.extend(kwargs.get("tools", []))
        block = MagicMock(); block.type = "text"; block.text = "ok"
        resp = MagicMock(); resp.stop_reason = "end_turn"; resp.content = [block]
        return resp

    client = MagicMock()
    client.messages.create = fake_create

    reg = ToolRegistry()
    reg.register({
        "tool_a": {"schema": {"name": "tool_a", "description": "a", "input_schema": {"type": "object", "properties": {}, "required": []}}, "executor": AsyncMock()},
        "tool_b": {"schema": {"name": "tool_b", "description": "b", "input_schema": {"type": "object", "properties": {}, "required": []}}, "executor": AsyncMock()},
    })

    runner = AgentRunner(client, reg)
    blueprint = db.get(Blueprint, "test_bp")

    context = MagicMock()
    context.get_history.return_value = []
    context.add = MagicMock()
    confirmation_store = MagicMock()
    confirmation_store.get.return_value = None

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.agent_runner.SessionLocal", lambda: _CM(db))
        await runner.run(
            blueprint=blueprint,
            group_jid="g@g.us",
            sender="phone@s.whatsapp.net",
            is_admin=False,
            message="hello",
            context=context,
            confirmation_store=confirmation_store,
        )

    tool_names = [t["name"] for t in captured_tools]
    assert "tool_a" in tool_names
    assert "tool_b" not in tool_names  # globally disabled


# ── Helpers shared by API tests ───────────────────────────────────────────────

class _SessionCM:
    def __init__(self, factory):
        self._factory = factory
        self._sess = None
    def __enter__(self):
        self._sess = self._factory()
        return self._sess
    def __exit__(self, *a):
        if self._sess:
            self._sess.close()


# ── GET /admin/api/tools ──────────────────────────────────────────────────────

def test_list_tools_returns_all_registered(db):
    _seed(db)
    reg = _make_registry(
        ("tool_a", "accounting"),
        ("tool_b", "automation"),
        ("tool_c", "invoices"),
    )
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.get("/admin/api/tools")

    assert resp.status_code == 200
    data = resp.json()
    names = [t["name"] for t in data]
    assert "tool_a" in names
    assert "tool_b" in names
    assert "tool_c" in names
    tool_a = next(t for t in data if t["name"] == "tool_a")
    assert tool_a["category"] == "accounting"
    assert "family_accounting" in tool_a["blueprints_using"]
    assert tool_a["globally_enabled"] is True


def test_list_tools_reflects_disabled_status(db):
    _seed(db)
    db.add(SystemConfig(key="disabled_tools", value=json.dumps(["tool_b"])))
    db.commit()
    reg = _make_registry(("tool_a", "accounting"), ("tool_b", "automation"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.get("/admin/api/tools")

    data = resp.json()
    tool_b = next(t for t in data if t["name"] == "tool_b")
    assert tool_b["globally_enabled"] is False


# ── PATCH /admin/api/blueprints/{id}/tools ────────────────────────────────────

def test_update_blueprint_tools_saves_to_db(db):
    _seed(db)
    reg = _make_registry(("tool_a", "accounting"), ("tool_b", "automation"), ("tool_c", "invoices"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.patch(
            "/admin/api/blueprints/family_accounting/tools",
            json={"tools_enabled": ["tool_a", "tool_c"]},
        )

    assert resp.status_code == 200
    db.expire_all()
    bp = db.get(Blueprint, "family_accounting")
    assert set(json.loads(bp.tools_enabled)) == {"tool_a", "tool_c"}


def test_update_blueprint_tools_rejects_unknown_tools(db):
    _seed(db)
    reg = _make_registry(("tool_a", "accounting"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.patch(
            "/admin/api/blueprints/family_accounting/tools",
            json={"tools_enabled": ["tool_a", "nonexistent_tool"]},
        )

    assert resp.status_code == 400


# ── PATCH /admin/api/tools/{name}/enabled ─────────────────────────────────────

def test_disable_tool_writes_system_config(db):
    _seed(db)
    reg = _make_registry(("tool_a", "accounting"), ("tool_b", "automation"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.patch("/admin/api/tools/tool_b/enabled", json={"enabled": False})

    assert resp.status_code == 200
    db.expire_all()
    row = db.get(SystemConfig, "disabled_tools")
    assert row is not None
    assert "tool_b" in json.loads(row.value)


def test_reenable_tool_removes_from_system_config(db):
    _seed(db)
    db.add(SystemConfig(key="disabled_tools", value=json.dumps(["tool_a", "tool_b"])))
    db.commit()
    reg = _make_registry(("tool_a", "accounting"), ("tool_b", "automation"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.patch("/admin/api/tools/tool_b/enabled", json={"enabled": True})

    assert resp.status_code == 200
    db.expire_all()
    row = db.get(SystemConfig, "disabled_tools")
    disabled = json.loads(row.value)
    assert "tool_b" not in disabled
    assert "tool_a" in disabled


def test_disable_unknown_tool_returns_404(db):
    reg = _make_registry(("tool_a", "accounting"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.patch("/admin/api/tools/not_real/enabled", json={"enabled": False})

    assert resp.status_code == 404


# ── DELETE /admin/api/tools/{name}/blueprints ─────────────────────────────────

def test_remove_tool_from_all_blueprints(db):
    _seed(db)  # tool_b is in both family_accounting and invoice_curator
    reg = _make_registry(("tool_a", "accounting"), ("tool_b", "automation"), ("tool_c", "invoices"))
    registry_ref.set_registry(reg)
    app = _make_app(db)

    class _CM:
        def __init__(self, s): self._s = s
        def __enter__(self): return self._s
        def __exit__(self, *a): pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("app.admin.api.SessionLocal", lambda: _CM(db))
        client = TestClient(app)
        resp = client.delete("/admin/api/tools/tool_b/blueprints")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data["blueprints_updated"]) == {"family_accounting", "invoice_curator"}
    db.expire_all()
    for bp_id in ["family_accounting", "invoice_curator"]:
        bp = db.get(Blueprint, bp_id)
        assert "tool_b" not in json.loads(bp.tools_enabled)
