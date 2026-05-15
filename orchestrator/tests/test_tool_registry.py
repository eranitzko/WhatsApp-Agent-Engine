import pytest
from unittest.mock import AsyncMock
from app.tool_registry import ToolRegistry

HELLO_SCHEMA = {
    "name": "say_hello",
    "description": "Says hello",
    "input_schema": {"type": "object", "properties": {}},
}

@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register({
        "say_hello": {
            "schema": HELLO_SCHEMA,
            "executor": AsyncMock(return_value="hello"),
        }
    })
    return r


def test_get_schemas_returns_only_requested(registry):
    schemas = registry.get_schemas(["say_hello"])
    assert len(schemas) == 1
    assert schemas[0]["name"] == "say_hello"


def test_get_schemas_ignores_unknown_names(registry):
    schemas = registry.get_schemas(["say_hello", "nonexistent"])
    assert len(schemas) == 1


def test_has_tool_returns_true_for_registered(registry):
    assert registry.has_tool("say_hello") is True


def test_has_tool_returns_false_for_unknown(registry):
    assert registry.has_tool("unknown") is False


@pytest.mark.asyncio
async def test_execute_known_tool(registry):
    result = await registry.execute("say_hello", {})
    assert result == "hello"


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error_string(registry):
    result = await registry.execute("nonexistent", {})
    assert "Unknown tool" in result


def test_register_merges_multiple_tool_sets(registry):
    registry.register({
        "say_bye": {
            "schema": {"name": "say_bye", "description": "Bye", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="bye"),
        }
    })
    assert registry.has_tool("say_hello") is True
    assert registry.has_tool("say_bye") is True
