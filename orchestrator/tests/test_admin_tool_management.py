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
