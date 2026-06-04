# Admin Panel Tool Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Blueprints tool editor and a Tools Registry panel to the admin web UI so sysadmins can wire tools to blueprints and globally disable tools without code deploys.

**Architecture:** A new `registry_ref.py` module exposes the live `ToolRegistry` to the admin API. Four new API endpoints power the UI. `AgentRunner` reads a `disabled_tools` list from `SystemConfig` and filters it from `allowed_tools` on every message. The frontend gains a new `#tools` route and an inline tool chip editor in the Blueprints panel.

**Tech Stack:** Python/FastAPI (backend), vanilla JS SPA (frontend), SQLAlchemy/SQLite (DB), pytest (tests).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `orchestrator/app/registry_ref.py` | Singleton reference to live ToolRegistry |
| Modify | `orchestrator/app/seeder.py` | Stop overwriting `tools_enabled` on blueprint update |
| Modify | `orchestrator/app/main.py` | Call `registry_ref.set_registry()` at startup |
| Modify | `orchestrator/app/agent_runner.py` | Filter globally disabled tools per message |
| Modify | `orchestrator/app/admin/api.py` | 4 new endpoints |
| Modify | `orchestrator/app/tools/automation_tools.py` | Add `"category": "automation"` to each schema |
| Modify | `orchestrator/app/tools/accounting_tools.py` | Add `"category": "accounting"` to each schema |
| Modify | `orchestrator/app/tools/invoice_tools.py` | Add `"category": "invoices"` to each schema |
| Modify | `orchestrator/app/tools/notion_tools.py` | Add `"category": "notion"` to each schema |
| Modify | `orchestrator/app/static/admin/style.css` | Chip, toggle, sortable-th, expand-row styles |
| Modify | `orchestrator/app/static/admin/app.js` | Enhanced Blueprints panel + new Tools panel |
| Create | `orchestrator/tests/test_admin_tool_management.py` | Tests for all new API endpoints |

---

## Task 1: `registry_ref.py` + seeder fix + main.py wiring

**Files:**
- Create: `orchestrator/app/registry_ref.py`
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/main.py`
- Create: `orchestrator/tests/test_admin_tool_management.py` (scaffold only)

- [ ] **Step 1: Create `registry_ref.py`**

Create `orchestrator/app/registry_ref.py`:

```python
"""Shared reference to the live ToolRegistry. Set by main.py at startup.

Import get_registry() anywhere that needs access to the live tool list
without creating circular imports through main.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tool_registry import ToolRegistry

_registry: "ToolRegistry | None" = None


def set_registry(r: "ToolRegistry") -> None:
    """Called once by main.py lifespan after all tools are registered."""
    global _registry
    _registry = r


def get_registry() -> "ToolRegistry":
    """Return the live ToolRegistry. Raises RuntimeError if not yet set."""
    if _registry is None:
        raise RuntimeError("ToolRegistry not yet initialised — call set_registry() first")
    return _registry
```

- [ ] **Step 2: Fix seeder — stop overwriting tools_enabled on update**

In `orchestrator/app/seeder.py`, find the update path for DEFAULT_BLUEPRINTS and remove the `tools_enabled` line:

```python
    # Static blueprints — upsert on each startup so DB stays in sync
    for bp_data in DEFAULT_BLUEPRINTS:
        existing = db.query(Blueprint).filter_by(id=bp_data["id"]).first()
        if existing:
            existing.model = bp_data["model"]
            # NOTE: do NOT overwrite tools_enabled — admin UI changes must persist across restarts
        else:
            db.add(Blueprint(**bp_data))
```

- [ ] **Step 3: Wire registry_ref in main.py**

In `orchestrator/app/main.py`, after the `tool_registry.register(get_automation_tools())` call in the lifespan function, add:

```python
    from app import registry_ref
    registry_ref.set_registry(tool_registry)
```

- [ ] **Step 4: Create test scaffold**

Create `orchestrator/tests/test_admin_tool_management.py`:

```python
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
```

- [ ] **Step 5: Run full suite to verify nothing broken**

```bash
cd orchestrator
python -m pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/registry_ref.py \
        orchestrator/app/seeder.py \
        orchestrator/app/main.py \
        orchestrator/tests/test_admin_tool_management.py
git commit -m "feat: registry_ref module, seeder stops overwriting tools_enabled"
```

---

## Task 2: Add `category` to all tool schemas

**Files:**
- Modify: `orchestrator/app/tools/automation_tools.py`
- Modify: `orchestrator/app/tools/accounting_tools.py`
- Modify: `orchestrator/app/tools/invoice_tools.py`
- Modify: `orchestrator/app/tools/notion_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `orchestrator/tests/test_admin_tool_management.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_admin_tool_management.py::test_all_tools_have_category_field -v
```

Expected: FAILED — `AssertionError: automation tool create_automation missing category`

- [ ] **Step 3: Add category to `automation_tools.py`**

In `orchestrator/app/tools/automation_tools.py`, add `"category": "automation"` to every entry in `_SCHEMAS`. Each schema dict has a `"name"` key — add `"category"` at the same level:

```python
_SCHEMAS: dict[str, dict] = {
    "create_automation": {
        "name": "create_automation",
        "category": "automation",
        "description": (
            ...
        ),
        ...
    },
    "confirm_automation": {
        "name": "confirm_automation",
        "category": "automation",
        ...
    },
    "list_automations": {
        "name": "list_automations",
        "category": "automation",
        ...
    },
    "pause_automation": {
        "name": "pause_automation",
        "category": "automation",
        ...
    },
    "cancel_automation": {
        "name": "cancel_automation",
        "category": "automation",
        ...
    },
}
```

Add `"category": "automation"` as the second key in each of the five schema dicts.

- [ ] **Step 4: Add category to `accounting_tools.py`**

In `orchestrator/app/tools/accounting_tools.py`, the `_SCHEMAS` dict has entries like `"record_transaction": {"name": ..., "description": ..., "input_schema": ...}`. Add `"category": "accounting"` to each of the 14 entries. The simplest edit: add it as the second key after `"name"` in every schema dict inside `_SCHEMAS`.

- [ ] **Step 5: Add category to `invoice_tools.py`**

In `orchestrator/app/tools/invoice_tools.py`, the `_SCHEMA_BY_NAME` is built from a comprehension. Change it to add the category:

```python
_SCHEMA_BY_NAME: dict[str, dict] = {
    s["name"]: ({k: v for k, v in s.items() if k != "cache_control"} | {"category": "invoices"})
    for s in _orig.TOOL_SCHEMAS
}
```

- [ ] **Step 6: Add category to `notion_tools.py`**

In `orchestrator/app/tools/notion_tools.py`, add `"category": "notion"` to the schema of each of the 4 tools in the `return` dict. Each schema dict currently has `"name"`, `"description"`, `"input_schema"` — add `"category": "notion"` after `"name"`.

- [ ] **Step 7: Run test to verify it passes**

```bash
python -m pytest tests/test_admin_tool_management.py::test_all_tools_have_category_field -v
```

Expected: PASSED.

- [ ] **Step 8: Run full suite**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add orchestrator/app/tools/automation_tools.py \
        orchestrator/app/tools/accounting_tools.py \
        orchestrator/app/tools/invoice_tools.py \
        orchestrator/app/tools/notion_tools.py \
        orchestrator/tests/test_admin_tool_management.py
git commit -m "feat: add category field to all tool schemas"
```

---

## Task 3: AgentRunner global disable filter

**Files:**
- Modify: `orchestrator/app/agent_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `orchestrator/tests/test_admin_tool_management.py`:

```python
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

    client = MagicMock(spec=anthropic.AsyncAnthropic)
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

    # Patch SessionLocal in agent_runner to use our test db
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_admin_tool_management.py::test_agent_runner_filters_globally_disabled_tools -v
```

Expected: FAILED — `AssertionError: assert 'tool_b' not in ['tool_a', 'tool_b']`

- [ ] **Step 3: Add global disable filter to AgentRunner**

In `orchestrator/app/agent_runner.py`, replace line 31:

```python
        allowed_tools = blueprint.tools_list()
```

with:

```python
        allowed_tools = blueprint.tools_list()

        # Filter globally disabled tools (stored in SystemConfig["disabled_tools"])
        try:
            from app.db.session import SessionLocal
            from app.db.models import SystemConfig
            import json as _json
            with SessionLocal() as _db:
                _row = _db.get(SystemConfig, "disabled_tools")
                if _row and _row.value:
                    _disabled = set(_json.loads(_row.value))
                    allowed_tools = [t for t in allowed_tools if t not in _disabled]
        except Exception:
            logger.warning("Could not read disabled_tools from SystemConfig", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_admin_tool_management.py::test_agent_runner_filters_globally_disabled_tools -v
```

Expected: PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/agent_runner.py orchestrator/tests/test_admin_tool_management.py
git commit -m "feat: AgentRunner filters globally disabled tools from SystemConfig"
```

---

## Task 4: New admin API endpoints

**Files:**
- Modify: `orchestrator/app/admin/api.py`

- [ ] **Step 1: Write the failing tests**

Append to `orchestrator/tests/test_admin_tool_management.py`:

```python
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


def _make_client(db, reg):
    """Build a TestClient with the given db and registry injected."""
    app = _make_app(db)
    registry_ref.set_registry(reg)
    Session = type("S", (), {"__call__": lambda self: db})()

    import app.admin.api as admin_api
    import app.agent_runner as agent_runner_mod
    admin_api._session_factory = lambda: _SessionCM(lambda: db)

    # Patch SessionLocal inside admin.api
    import unittest.mock as mock
    client = TestClient(app)
    return client, mock


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
    # Check structure of one entry
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
    assert "tool_a" in disabled  # tool_a still disabled


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
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_admin_tool_management.py -k "test_list_tools or test_update_blueprint or test_disable or test_reenable or test_remove" -v
```

Expected: errors — endpoints do not exist yet.

- [ ] **Step 3: Implement the 4 new endpoints in `admin/api.py`**

Add the following to `orchestrator/app/admin/api.py` after the existing Blueprints section. First add `SystemConfig` to the models import at the top:

```python
from app.db.models import AdminNumbers, Blueprint, GroupRegistry, SystemConfig
```

Then append the new sections:

```python
# -- Tools -------------------------------------------------------------------

@router.get("/tools", dependencies=[Depends(require_auth)])
def list_tools():
    """Return all tools registered in the live ToolRegistry."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    with SessionLocal() as db:
        # Build disabled set
        disabled_row = db.get(SystemConfig, "disabled_tools")
        disabled = set(_json.loads(disabled_row.value)) if disabled_row and disabled_row.value else set()

        # Build tool → [blueprint_id] map
        tool_to_bps: dict[str, list[str]] = {}
        for bp in db.query(Blueprint).all():
            try:
                for t in _json.loads(bp.tools_enabled or "[]"):
                    tool_to_bps.setdefault(t, []).append(bp.id)
            except _json.JSONDecodeError:
                pass

    result = []
    for name, entry in reg._tools.items():
        schema = entry["schema"]
        result.append({
            "name": name,
            "description": schema.get("description", ""),
            "category": schema.get("category", "other"),
            "blueprints_using": tool_to_bps.get(name, []),
            "globally_enabled": name not in disabled,
        })
    return sorted(result, key=lambda x: x["name"])


class UpdateBlueprintToolsRequest(BaseModel):
    tools_enabled: list[str]


@router.patch("/blueprints/{blueprint_id}/tools", dependencies=[Depends(require_auth)])
def update_blueprint_tools(blueprint_id: str, body: UpdateBlueprintToolsRequest):
    """Update tools_enabled for a blueprint. Validates all names against live registry."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    unknown = [t for t in body.tools_enabled if not reg.has_tool(t)]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")

    with SessionLocal() as db:
        bp = db.get(Blueprint, blueprint_id)
        if not bp:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        bp.tools_enabled = _json.dumps(body.tools_enabled)
        db.commit()
    return {"ok": True}


class UpdateToolEnabledRequest(BaseModel):
    enabled: bool


@router.patch("/tools/{tool_name}/enabled", dependencies=[Depends(require_auth)])
def update_tool_enabled(tool_name: str, body: UpdateToolEnabledRequest):
    """Globally enable or disable a tool via SystemConfig['disabled_tools']."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    if not reg.has_tool(tool_name):
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not in registry")

    with SessionLocal() as db:
        row = db.get(SystemConfig, "disabled_tools")
        disabled = set(_json.loads(row.value)) if row and row.value else set()

        if body.enabled:
            disabled.discard(tool_name)
        else:
            disabled.add(tool_name)

        new_value = _json.dumps(sorted(disabled))
        if row:
            row.value = new_value
        else:
            db.add(SystemConfig(key="disabled_tools", value=new_value))
        db.commit()
    return {"ok": True}


@router.delete("/tools/{tool_name}/blueprints", dependencies=[Depends(require_auth)])
def remove_tool_from_blueprints(tool_name: str):
    """Remove a tool from every blueprint's tools_enabled list."""
    import json as _json
    updated: list[str] = []
    with SessionLocal() as db:
        for bp in db.query(Blueprint).all():
            try:
                tools = _json.loads(bp.tools_enabled or "[]")
            except _json.JSONDecodeError:
                continue
            if tool_name in tools:
                bp.tools_enabled = _json.dumps([t for t in tools if t != tool_name])
                updated.append(bp.id)
        db.commit()
    return {"ok": True, "blueprints_updated": updated}
```

- [ ] **Step 4: Run all tool management tests**

```bash
python -m pytest tests/test_admin_tool_management.py -v
```

Expected: all tests PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/admin/api.py orchestrator/tests/test_admin_tool_management.py
git commit -m "feat: admin API — GET /tools, PATCH blueprints tools, PATCH tool enabled, DELETE tool from blueprints"
```

---

## Task 5: CSS additions

**Files:**
- Modify: `orchestrator/app/static/admin/style.css`

No automated test — verified visually in Task 6 and 7.

- [ ] **Step 1: Append new CSS rules to `style.css`**

Append to the end of `orchestrator/app/static/admin/style.css`:

```css
/* ── Tool chip editor ───────────────────────────────────────────────────── */
.tool-chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px; background: var(--bg); border-radius: 8px; border: 1px solid var(--border); margin-top: 8px; }
.chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px 3px 6px; border-radius: 99px; font-size: 11px; border: 1px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; user-select: none; transition: background 0.1s, border-color 0.1s; }
.chip.on  { background: #eff6ff; border-color: #93c5fd; color: var(--accent); }
.chip.off { background: #f8fafc; border-color: #e2e8f0; color: #94a3b8; text-decoration: line-through; }
.chip-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.chip.on  .chip-dot { background: var(--accent); }
.chip.off .chip-dot { background: #cbd5e1; }
.chip-save-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.chip-save-row .hint { font-size: 11px; color: var(--muted); }

/* ── Expand row (tool editor beneath blueprint row) ─────────────────────── */
.expand-row td { padding: 0 !important; }
.expand-inner { padding: 14px 16px 16px; background: #f8fafc; border-bottom: 1px solid var(--border); }
.expand-inner h4 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); }

/* ── Toggle switch ──────────────────────────────────────────────────────── */
.toggle { position: relative; display: inline-block; width: 36px; height: 20px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.toggle .slider { position: absolute; cursor: pointer; inset: 0; background: #cbd5e1; border-radius: 20px; transition: 0.2s; }
.toggle .slider::before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.2s; }
.toggle input:checked + .slider { background: var(--accent); }
.toggle input:checked + .slider::before { transform: translateX(16px); }

/* ── Group pills (blueprint tags in Tools table) ────────────────────────── */
.group-pills { display: flex; flex-wrap: wrap; gap: 4px; }
.group-pill { font-size: 10px; background: var(--surface2); color: var(--accent); padding: 2px 7px; border-radius: 99px; white-space: nowrap; }

/* ── Sortable table headers ─────────────────────────────────────────────── */
.table th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
.table th.sortable:hover { color: var(--accent); }
.table th.sort-asc  .sort-icon::after { content: ' ↑'; color: var(--accent); }
.table th.sort-desc .sort-icon::after { content: ' ↓'; color: var(--accent); }
.table th:not(.sort-asc):not(.sort-desc) .sort-icon::after { content: ' ↕'; opacity: 0.3; }

/* ── Tab bar (Tools category filter) ───────────────────────────────────── */
.tab-bar { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
.tab { padding: 8px 16px; cursor: pointer; font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -1px; transition: color 0.15s; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 500; }
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/app/static/admin/style.css
git commit -m "feat: admin CSS — chip editor, toggle, sortable th, tab bar, expand row"
```

---

## Task 6: Frontend — Enhanced Blueprints panel

**Files:**
- Modify: `orchestrator/app/static/admin/app.js`

- [ ] **Step 1: Replace `renderBlueprints` with the enhanced version**

In `orchestrator/app/static/admin/app.js`, find and replace the entire `renderBlueprints` function and its helpers (`expandPrompt`, `collapsePrompt`) with:

```javascript
// ── Blueprints (enhanced with tool editor) ────────────────────────────────────

async function renderBlueprints(app) {
  app.innerHTML = layout('blueprints', '<p style="color:var(--muted)">Loading...</p>');
  const [bpRes, toolsRes] = await Promise.all([
    apiFetch('/blueprints'),
    apiFetch('/tools'),
  ]);
  if (!bpRes || !toolsRes) return;
  const blueprints = await bpRes.json();
  const allTools   = await toolsRes.json();  // [{name, category, ...}]

  const rows = blueprints.map((b, i) => {
    const enabledSet = new Set(JSON.parse(b.tools_list || '[]'));
    const chipsHtml = allTools.map(t => {
      const on = enabledSet.has(t.name);
      return `<div class="chip ${on ? 'on' : 'off'}" data-tool="${escAttr(t.name)}" onclick="toggleChip(this)">
        <span class="chip-dot"></span>${escHtml(t.name)}
      </div>`;
    }).join('');

    return `
      <tr>
        <td>
          <strong>${escHtml(b.display_name)}</strong><br>
          <span style="font-size:11px;color:var(--muted)">${escHtml(b.id)}</span>
        </td>
        <td><span class="badge">${b.tools_count} tools</span></td>
        <td>
          <div class="bp-expand-wrap" id="bp-wrap-${i}">
            <div class="bp-prompt bp-collapsed" id="bp-prompt-${i}"
                 onclick="expandPrompt(${i})" title="Click to expand">
              ${escHtml(b.system_prompt_preview)}…
            </div>
            <textarea class="bp-full" id="bp-full-${i}" readonly
              onblur="collapsePrompt(${i})" style="display:none">${escHtml(b.system_prompt)}</textarea>
          </div>
        </td>
        <td style="text-align:right">
          <button class="btn btn-primary" style="font-size:12px;padding:5px 12px"
            onclick="toggleToolEditor(${i})">🔧 Edit tools</button>
        </td>
      </tr>
      <tr class="expand-row" id="tool-editor-${i}" style="display:none">
        <td colspan="4">
          <div class="expand-inner">
            <h4>Enabled tools — ${escHtml(b.id)}</h4>
            <div class="tool-chips" id="chips-${i}">
              ${chipsHtml}
            </div>
            <div class="chip-save-row">
              <button class="btn btn-primary" style="font-size:12px;padding:5px 12px"
                onclick="saveBlueprintTools('${escAttr(b.id)}', ${i})">Save changes</button>
              <span class="hint">Click chips to toggle · Blue = enabled · Grey = disabled</span>
            </div>
          </div>
        </td>
      </tr>`;
  }).join('');

  app.innerHTML = layout('blueprints', `
    <div class="page-header"><h2>Blueprints</h2></div>
    <div class="table-wrap"><table class="table">
      <thead>
        <tr>
          <th>Blueprint</th><th>Tools</th><th>System Prompt</th><th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table></div>`);
}

function toggleChip(el) {
  el.classList.toggle('on');
  el.classList.toggle('off');
}

function toggleToolEditor(i) {
  const row = document.getElementById('tool-editor-' + i);
  row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function saveBlueprintTools(blueprintId, i) {
  const chips = document.querySelectorAll(`#chips-${i} .chip.on`);
  const tools = Array.from(chips).map(c => c.dataset.tool);
  const res = await apiFetch('/blueprints/' + encodeURIComponent(blueprintId) + '/tools', {
    method: 'PATCH',
    body: JSON.stringify({ tools_enabled: tools }),
  });
  if (res && res.ok) {
    renderBlueprints(document.getElementById('app'));
  } else {
    alert('Failed to save tools. Check that all tool names are valid.');
  }
}

function expandPrompt(i) {
  document.getElementById('bp-prompt-' + i).style.display = 'none';
  const ta = document.getElementById('bp-full-' + i);
  ta.style.display = 'block';
  ta.focus();
}

function collapsePrompt(i) {
  document.getElementById('bp-full-' + i).style.display = 'none';
  document.getElementById('bp-prompt-' + i).style.display = '';
}
```

Note: the existing `GET /admin/api/blueprints` response includes `tools_count` and `system_prompt_preview` but not the full `tools_list`. The enhanced `renderBlueprints` needs `tools_list` (the actual JSON array). Update the `list_blueprints` endpoint in `api.py` to also return `tools_list`:

In `orchestrator/app/admin/api.py`, in the `list_blueprints` function, add `"tools_list": b.tools_enabled or "[]"` to the result dict:

```python
            result.append({
                "id": b.id,
                "display_name": b.display_name,
                "tools_count": tools_count,
                "tools_list": b.tools_enabled or "[]",
                "system_prompt": b.system_prompt or "",
                "system_prompt_preview": b.system_prompt[:100] if b.system_prompt else "",
            })
```

- [ ] **Step 2: Verify manually**

```bash
# In orchestrator/:
python -m pytest tests/ -q
```

Expected: all tests pass. Then open the admin panel in a browser, navigate to Blueprints, click "Edit tools" on a blueprint — the chip editor should expand with all tools shown, chips toggled correctly. Click a chip to toggle, click Save — the panel reloads with the updated count.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/static/admin/app.js orchestrator/app/admin/api.py
git commit -m "feat: Blueprints panel — inline tool chip editor with save"
```

---

## Task 7: Frontend — Tools Registry panel + nav update

**Files:**
- Modify: `orchestrator/app/static/admin/app.js`

- [ ] **Step 1: Add Tools to nav and route**

In `orchestrator/app/static/admin/app.js`, find the `layout` function's `nav` array:

```javascript
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'admins',     icon: '👥', label: 'Admins' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
  ];
```

Replace with:

```javascript
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'admins',     icon: '👥', label: 'Admins' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
    { hash: 'tools',      icon: '🔧', label: 'Tools' },
  ];
```

In the `route` function, add:

```javascript
  else if (hash === 'tools') await renderTools(app);
```

- [ ] **Step 2: Add `renderTools` and supporting functions**

Append to `orchestrator/app/static/admin/app.js`:

```javascript
// ── Tools Registry ─────────────────────────────────────────────────────────────

let _toolsData = [];       // full tool list from API
let _toolsSortCol = 0;     // 0=name 1=category 2=used-by 3=enabled
let _toolsSortDir = 1;     // 1=asc -1=desc
let _toolsFilter  = 'all'; // category filter

async function renderTools(app) {
  app.innerHTML = layout('tools', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/tools');
  if (!res) return;
  _toolsData = await res.json();
  _toolsSortCol = 0; _toolsSortDir = 1; _toolsFilter = 'all';

  // Derive unique categories
  const cats = [...new Set(_toolsData.map(t => t.category))].sort();
  const tabsHtml = `
    <div class="tab-bar">
      <div class="tab active" onclick="toolsFilterCat('all', this)">All (${_toolsData.length})</div>
      ${cats.map(c => {
        const n = _toolsData.filter(t => t.category === c).length;
        return `<div class="tab" onclick="toolsFilterCat('${escAttr(c)}', this)">${escHtml(c)} (${n})</div>`;
      }).join('')}
    </div>`;

  app.innerHTML = layout('tools', `
    <div class="page-header"><h2>Tools Registry</h2></div>
    <p style="font-size:13px;color:var(--muted);margin:0 0 16px">
      All tools registered in the engine. Toggle global availability or see which blueprints use each.
      Changes take effect immediately — no restart needed.
    </p>
    ${tabsHtml}
    <div id="tools-table-wrap"></div>`);

  _renderToolsTable();
}

function _renderToolsTable() {
  let rows = _toolsData.filter(t => _toolsFilter === 'all' || t.category === _toolsFilter);

  rows.sort((a, b) => {
    let av, bv;
    switch (_toolsSortCol) {
      case 0: av = a.name;                   bv = b.name; break;
      case 1: av = a.category;               bv = b.category; break;
      case 2: av = a.blueprints_using.join(); bv = b.blueprints_using.join(); break;
      case 3: av = a.globally_enabled ? 1 : 0; bv = b.globally_enabled ? 1 : 0; break;
    }
    return (av > bv ? 1 : av < bv ? -1 : 0) * _toolsSortDir;
  });

  function thCls(col) {
    if (_toolsSortCol !== col) return 'sortable';
    return 'sortable ' + (_toolsSortDir === 1 ? 'sort-asc' : 'sort-desc');
  }

  const tableHtml = `
    <table class="table">
      <thead><tr>
        <th class="${thCls(0)}" onclick="toolsSort(0)"><span class="sort-icon">Tool name</span></th>
        <th class="${thCls(1)}" onclick="toolsSort(1)"><span class="sort-icon">Category</span></th>
        <th class="${thCls(2)}" onclick="toolsSort(2)"><span class="sort-icon">Used by</span></th>
        <th class="${thCls(3)}" style="text-align:center" onclick="toolsSort(3)"><span class="sort-icon">Enabled</span></th>
        <th></th>
      </tr></thead>
      <tbody>
        ${rows.map(t => `
          <tr style="${t.globally_enabled ? '' : 'opacity:0.55'}">
            <td>
              <strong>${escHtml(t.name)}</strong><br>
              <span style="font-size:11px;color:var(--muted)">${escHtml(t.description)}</span>
            </td>
            <td><span class="badge">${escHtml(t.category)}</span></td>
            <td><div class="group-pills">${t.blueprints_using.map(b =>
              `<span class="group-pill">${escHtml(b)}</span>`).join('')}</div></td>
            <td style="text-align:center">
              <label class="toggle">
                <input type="checkbox" ${t.globally_enabled ? 'checked' : ''}
                  onchange="toggleToolEnabled('${escAttr(t.name)}', this.checked)">
                <span class="slider"></span>
              </label>
            </td>
            <td style="text-align:right">
              <button class="btn btn-danger"
                onclick="removeToolFromAllBlueprints('${escAttr(t.name)}')">Remove</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>
    <p style="font-size:11px;color:var(--muted);margin-top:12px">
      ${rows.length} tool${rows.length !== 1 ? 's' : ''} shown${_toolsFilter !== 'all' ? ` in "${_toolsFilter}"` : ''}.
    </p>`;

  document.getElementById('tools-table-wrap').innerHTML = tableHtml;
}

function toolsSort(col) {
  if (_toolsSortCol === col) _toolsSortDir *= -1;
  else { _toolsSortCol = col; _toolsSortDir = 1; }
  _renderToolsTable();
}

function toolsFilterCat(cat, tab) {
  _toolsFilter = cat;
  document.querySelectorAll('.tab-bar .tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  _renderToolsTable();
}

async function toggleToolEnabled(toolName, enabled) {
  const res = await apiFetch('/tools/' + encodeURIComponent(toolName) + '/enabled', {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
  if (res && res.ok) {
    const t = _toolsData.find(x => x.name === toolName);
    if (t) t.globally_enabled = enabled;
    _renderToolsTable();
  } else {
    alert('Failed to update tool status.');
    _renderToolsTable(); // revert toggle
  }
}

async function removeToolFromAllBlueprints(toolName) {
  if (!confirm(`Remove "${toolName}" from all blueprints? This cannot be undone from the UI.`)) return;
  const res = await apiFetch('/tools/' + encodeURIComponent(toolName) + '/blueprints', {
    method: 'DELETE',
  });
  if (res && res.ok) {
    const data = await res.json();
    const updated = data.blueprints_updated;
    alert(`Removed from ${updated.length} blueprint${updated.length !== 1 ? 's' : ''}: ${updated.join(', ')}`);
    // Refresh tool data
    const fresh = await apiFetch('/tools');
    if (fresh) _toolsData = await fresh.json();
    _renderToolsTable();
  } else {
    alert('Failed to remove tool.');
  }
}
```

- [ ] **Step 2: Run full suite to make sure nothing broke**

```bash
cd orchestrator
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/static/admin/app.js
git commit -m "feat: Tools Registry panel with sortable columns, category tabs, enable/disable, remove"
```

---

## Task 8: Push to GitHub

- [ ] **Step 1: Final test run**

```bash
cd orchestrator
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Push**

```bash
git push origin HEAD
```

Expected: branch updated on remote.

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| `registry_ref.py` module | Task 1 |
| Seeder stops overwriting `tools_enabled` | Task 1 |
| `registry_ref.set_registry()` called at startup | Task 1 |
| `"category"` field in all tool schemas | Task 2 |
| AgentRunner filters `disabled_tools` | Task 3 |
| `GET /admin/api/tools` | Task 4 |
| `PATCH /admin/api/blueprints/{id}/tools` | Task 4 |
| `PATCH /admin/api/tools/{name}/enabled` | Task 4 |
| `DELETE /admin/api/tools/{name}/blueprints` | Task 4 |
| CSS: chip, toggle, sortable-th, tab-bar, expand-row | Task 5 |
| Blueprints panel — inline tool chip editor | Task 6 |
| `GET /admin/api/blueprints` returns `tools_list` | Task 6 |
| Tools Registry panel with sortable columns | Task 7 |
| Category filter tabs | Task 7 |
| Enable/disable toggle | Task 7 |
| Remove from all blueprints | Task 7 |
| Tools nav item in sidebar + bottom nav | Task 7 |

All requirements covered. ✅
