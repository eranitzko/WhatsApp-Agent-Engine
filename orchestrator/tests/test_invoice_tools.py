import inspect
from unittest.mock import MagicMock

import pytest
from app.tools.invoice_tools import get_invoice_tools

EXPECTED_TOOLS = [
    "get_status", "list_invoices", "get_preview", "update_config",
    "flag_invoice", "unflag_invoice", "set_invoice_date",
    "set_invoice_amount", "add_date_format", "request_confirmation",
]

def test_get_invoice_tools_returns_all_10_tools():
    tools = get_invoice_tools()
    assert set(tools.keys()) == set(EXPECTED_TOOLS)

def test_each_tool_has_schema_and_executor():
    tools = get_invoice_tools()
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name

def test_get_invoice_tools_accepts_db_session_factory():
    # Should not raise TypeError
    tools = get_invoice_tools(db_session_factory=None)
    assert len(tools) == 10

@pytest.mark.asyncio
async def test_request_confirmation_without_store_returns_error():
    tools = get_invoice_tools()
    result = await tools["request_confirmation"]["executor"](
        {"action": "remove_invoice", "params": {}, "description": "Remove invoice"},
        group_jid="123@g.us",
        confirmation_store=None,
    )
    assert "Error" in result or "not available" in result

@pytest.mark.asyncio
async def test_request_confirmation_calls_store():
    mock_store = MagicMock()
    tools = get_invoice_tools()
    result = await tools["request_confirmation"]["executor"](
        {"action": "remove_invoice", "params": {"invoice_id": "abc"}, "description": "Remove invoice abc"},
        group_jid="123@g.us",
        confirmation_store=mock_store,
    )
    mock_store.set.assert_called_once_with("123@g.us", "remove_invoice", {"invoice_id": "abc"}, "Remove invoice abc")
    assert "yes" in result.lower() or "confirm" in result.lower()

def test_system_prompt_is_substantial():
    from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
    assert len(INVOICE_CURATOR_SYSTEM_PROMPT) > 500


def test_each_schema_has_required_keys():
    tools = get_invoice_tools()
    for name, entry in tools.items():
        missing = {"name", "description", "input_schema"} - entry["schema"].keys()
        assert not missing, f"{name}: schema missing keys {missing}"


def test_get_status_executor_is_async_callable():
    tools = get_invoice_tools()
    executor = tools["get_status"]["executor"]
    assert callable(executor)
    assert inspect.iscoroutinefunction(executor)


def test_multiple_calls_return_fresh_executors():
    tools_a = get_invoice_tools()
    tools_b = get_invoice_tools()
    wrapped_tools = [n for n in EXPECTED_TOOLS if n != "request_confirmation"]
    for name in wrapped_tools:
        assert tools_a[name]["executor"] is not tools_b[name]["executor"], (
            f"{name}: executor is the same object across calls"
        )
