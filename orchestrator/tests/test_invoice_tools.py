import inspect
from unittest.mock import MagicMock

import pytest
from app.tools.invoice_tools import get_invoice_tools
from tests.conftest import SessionCM

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

@pytest.mark.asyncio
async def test_stage_action_uses_resolved_phone_over_raw_sender():
    """Regression: staged_by must be the resolved canonical phone, not the
    raw sender JID/LID — using the raw value caused agent_runner's
    confirmation intercept (which compares against the resolved sender_phone)
    to permanently reject the original requester's own 'yes' whenever
    WhatsApp sent a LID instead of a phone number."""
    mock_store = MagicMock()
    tools = get_invoice_tools()
    await tools["stage_action"]["executor"](
        {"action": "remove_invoice", "params": {"invoice_id": "abc"}, "description": "Remove invoice abc"},
        group_jid="123@g.us",
        sender="175715853041683@lid",
        resolved_phone="972523206175",
        confirmation_store=mock_store,
    )
    mock_store.set.assert_called_once_with(
        "123@g.us", "remove_invoice", {"invoice_id": "abc"}, "Remove invoice abc",
        staged_by="972523206175",
    )


@pytest.mark.asyncio
async def test_stage_action_falls_back_to_raw_sender_without_resolved_phone():
    mock_store = MagicMock()
    tools = get_invoice_tools()
    await tools["stage_action"]["executor"](
        {"action": "remove_invoice", "params": {"invoice_id": "abc"}, "description": "Remove invoice abc"},
        group_jid="123@g.us",
        sender="972523206175@s.whatsapp.net",
        confirmation_store=mock_store,
    )
    mock_store.set.assert_called_once_with(
        "123@g.us", "remove_invoice", {"invoice_id": "abc"}, "Remove invoice abc",
        staged_by="972523206175",
    )


@pytest.mark.asyncio
async def test_stage_action_rejects_zero_amount_before_staging():
    """set_invoice_amount must be validated at staging time, not just at
    execution — otherwise a doomed action gets staged, the user confirms it,
    and only then does exec_set_invoice_amount reject it."""
    mock_store = MagicMock()
    tools = get_invoice_tools()
    result = await tools["stage_action"]["executor"](
        {"action": "set_invoice_amount", "params": {"invoice_id": "abc", "new_amount": 0},
         "description": "Set amount to 0"},
        group_jid="123@g.us",
        confirmation_store=mock_store,
    )
    assert "zero" in result.lower()
    mock_store.set.assert_not_called()


@pytest.mark.asyncio
async def test_stage_action_allows_negative_amount_for_set_invoice_amount():
    """Negative amounts ARE valid (refunds/returns) — only zero is rejected."""
    mock_store = MagicMock()
    tools = get_invoice_tools()
    result = await tools["stage_action"]["executor"](
        {"action": "set_invoice_amount", "params": {"invoice_id": "abc", "new_amount": -22.5},
         "description": "Set amount to -22.5"},
        group_jid="123@g.us",
        confirmation_store=mock_store,
    )
    mock_store.set.assert_called_once()
    assert "confirm" in result.lower()


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


# ── set_invoice_amount / save_invoice: negative amounts (refunds) allowed ─────

@pytest.mark.asyncio
async def test_exec_set_invoice_amount_allows_negative_for_refund(db):
    """Regression: invoices must support negative amounts for refunds/returns
    — the amount<=0 check previously rejected them outright."""
    from datetime import date
    from decimal import Decimal
    from unittest.mock import patch

    from app.agent.tools import exec_set_invoice_amount
    from app.db.models import Invoice

    db.add(Invoice(
        id="inv-neg", group_id="123@g.us", message_id="msg-neg", image_hash="hash-neg",
        invoice_date=date(2026, 7, 14), vendor="Acme",
        amount_original=Decimal("22.5"), currency_original="ILS", amount_ils=Decimal("22.5"),
    ))
    db.commit()

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)):
        result = await exec_set_invoice_amount(
            group_id="123@g.us", is_admin=True, invoice_id="inv-neg", new_amount=-22.5,
        )

    assert "error" not in result
    db.expire_all()
    invoice = db.get(Invoice, "inv-neg")
    assert invoice.amount_original == Decimal("-22.5")


@pytest.mark.asyncio
async def test_exec_set_invoice_amount_rejects_zero():
    from app.agent.tools import exec_set_invoice_amount
    result = await exec_set_invoice_amount(
        group_id="123@g.us", is_admin=True, invoice_id="whatever", new_amount=0,
    )
    assert "zero" in result["error"].lower()


@pytest.mark.asyncio
async def test_exec_save_invoice_allows_negative_for_refund(db):
    from unittest.mock import patch

    from app.agent.tools import exec_save_invoice

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)):
        result = await exec_save_invoice(
            group_id="123@g.us", is_admin=True,
            vendor="Acme", amount=-50.0, currency="ILS",
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_exec_save_invoice_rejects_zero():
    from app.agent.tools import exec_save_invoice
    result = await exec_save_invoice(
        group_id="123@g.us", is_admin=True, vendor="Acme", amount=0, currency="ILS",
    )
    assert "zero" in result["error"].lower()
