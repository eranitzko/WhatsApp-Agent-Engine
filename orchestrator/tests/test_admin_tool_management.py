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
