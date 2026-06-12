import inspect
from unittest.mock import MagicMock

import pytest
from app.tools.invoice_tools import get_invoice_tools

EXPECTED_TOOLS = [
    "get_status", "list_invoices", "get_invoice_summary", "update_config",
    "save_invoice", "flag_invoice", "unflag_invoice", "set_invoice_date",
    "set_invoice_amount", "add_date_format", "stage_action",
    # Confirmed-action executors (internal — not exposed to the agent)
    "remove_invoice", "send_email",
]

def test_get_invoice_tools_returns_all_tools():
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
    assert len(tools) == 13

@pytest.mark.asyncio
async def test_stage_action_without_store_returns_error():
    tools = get_invoice_tools()
    result = await tools["stage_action"]["executor"](
        {"action": "remove_invoice", "params": {}, "description": "Remove invoice"},
        group_jid="123@g.us",
        confirmation_store=None,
    )
    assert "Error" in result or "not available" in result

@pytest.mark.asyncio
async def test_stage_action_calls_store():
    mock_store = MagicMock()
    tools = get_invoice_tools()
    result = await tools["stage_action"]["executor"](
        {"action": "remove_invoice", "params": {"invoice_id": "abc"}, "description": "Remove invoice abc"},
        group_jid="123@g.us",
        confirmation_store=mock_store,
    )
    mock_store.set.assert_called_once_with("123@g.us", "remove_invoice", {"invoice_id": "abc"}, "Remove invoice abc", staged_by="")
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
    # Only _make_executor-wrapped tools create new closures on each call;
    # stage_action, remove_invoice, and send_email use module-level functions.
    _static_executors = {"stage_action", "remove_invoice", "send_email"}
    wrapped_tools = [n for n in EXPECTED_TOOLS if n not in _static_executors]
    for name in wrapped_tools:
        assert tools_a[name]["executor"] is not tools_b[name]["executor"], (
            f"{name}: executor is the same object across calls"
        )


def test_get_status_and_invoice_summary_descriptions_exclusive():
    """get_status must not mention stats; get_invoice_summary must not mention config."""
    from app.agent.tools import TOOL_SCHEMAS
    schemas = {s["name"]: s for s in TOOL_SCHEMAS}

    status_desc = schemas["get_status"]["description"].lower()
    summary_desc = schemas["get_invoice_summary"]["description"].lower()

    # get_status must be config-only
    assert "invoice count" not in status_desc
    assert "total" not in status_desc
    assert "get_invoice_summary" in status_desc  # cross-reference

    # get_invoice_summary must be stats-only
    assert "language" not in summary_desc
    assert "configuration" not in summary_desc
    assert "get_status" in summary_desc  # cross-reference


def test_no_negative_call_instructions_in_descriptions():
    from app.agent.tools import TOOL_SCHEMAS
    for schema in TOOL_SCHEMAS:
        desc = schema["description"].lower()
        assert "never call" not in desc, f"{schema['name']} contains 'never call'"
        assert "only execute after" not in desc, f"{schema['name']} contains 'only execute after'"


def test_stage_action_tool_exists():
    from app.tools.invoice_tools import get_invoice_tools
    tools = get_invoice_tools()
    assert "stage_action" in tools
    assert "request_confirmation" not in tools
