import inspect
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from app.tools.invoice_tools import get_invoice_tools
from tests.conftest import SessionCM, make_invoice

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


def test_system_prompt_requires_list_invoices_before_date_correction():
    """Regression: the agent was creating duplicate invoices by calling
    save_invoice to "fix" a wrong date instead of set_invoice_date, because
    the prompt's "always call list_invoices first" rule only covered delete
    and amount changes, not date corrections — so the agent had no nudge to
    look up the existing invoice's UUID before falling back to save_invoice."""
    from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
    prompt = INVOICE_CURATOR_SYSTEM_PROMPT.lower()
    assert "date correction" in prompt
    assert "save_invoice" in prompt.split("## invoice references")[1].split("## automations")[0]


def test_save_invoice_schema_forbids_correcting_existing_invoices():
    """Regression: save_invoice's own tool description must warn against
    using it to "fix" an existing invoice, since that creates a duplicate
    row instead of updating the original in place."""
    tools = get_invoice_tools()
    description = tools["save_invoice"]["schema"]["description"].lower()
    assert "duplicate" in description
    assert "set_invoice_date" in description


def test_system_prompt_forbids_save_invoice_on_pipeline_notification():
    """Regression: the agent kept calling save_invoice in response to the
    pipeline's own "Invoice auto-saved" notification (fed to it as a
    plain user-role message, textually indistinguishable from a human
    typing invoice details) — confirmed directly against a production
    duplicate where the invoice_number, vendor, amount, and date all
    matched an already-saved, already-photographed invoice exactly. The
    prompt's save_invoice guidance said "call immediately" for any message
    that looks like invoice details as text, with nothing distinguishing
    the system's own notification from a genuine user request."""
    from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
    prompt = INVOICE_CURATOR_SYSTEM_PROMPT.lower()
    assert "invoice auto-saved" in prompt


def test_save_invoice_schema_forbids_calling_on_pipeline_notification():
    """Same regression as above, reinforced in the tool's own description —
    the agent sees both the system prompt and every tool schema, so this
    guidance was added in both places, matching the existing pattern for
    the date-correction/save_invoice mixup fixed earlier."""
    tools = get_invoice_tools()
    description = tools["save_invoice"]["schema"]["description"].lower()
    assert "invoice auto-saved" in description


def test_set_invoice_date_schema_tells_agent_to_look_up_id_first():
    """Regression companion to the above: set_invoice_date's description
    must tell the agent to call list_invoices first rather than falling
    back to save_invoice when it lacks the invoice_id."""
    tools = get_invoice_tools()
    description = tools["set_invoice_date"]["schema"]["description"].lower()
    assert "list_invoices" in description
    assert "save_invoice" in description


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

    make_invoice(
        db, id="inv-neg", group_id="123@g.us",
        invoice_date=date(2026, 7, 14), vendor="Acme",
        amount_original=Decimal("22.5"), currency_original="ILS", amount_ils=Decimal("22.5"),
    )

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


@pytest.mark.asyncio
async def test_exec_save_invoice_rejects_exact_duplicate(db):
    """Regression: save_invoice had no duplicate check at all, unlike the
    image pipeline — the agent would sometimes call it redundantly right
    after an image was already successfully processed (e.g. misreading the
    pipeline's own "Invoice auto-saved" notification as a request to save
    it again), silently creating a duplicate row every time."""
    from datetime import date
    from unittest.mock import patch
    from app.agent.tools import exec_save_invoice
    from app.db.models import Invoice
    from tests.conftest import make_invoice

    make_invoice(
        db, group_id="123@g.us", vendor="Acme", amount_original=Decimal("30"),
        currency_original="ILS", invoice_date=date(2026, 8, 8),
    )

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)):
        result = await exec_save_invoice(
            group_id="123@g.us", is_admin=True,
            vendor="Acme", amount=30.0, currency="ILS", date="2026-08-08",
        )

    assert result.get("duplicate") is True
    assert result["duplicate_reason"] == "same_amount_and_date"
    assert db.query(Invoice).filter_by(group_id="123@g.us").count() == 1


@pytest.mark.asyncio
async def test_exec_save_invoice_rejects_duplicate_with_different_vendor(db):
    """Regression: vendor is deliberately NOT part of this check anymore —
    confirmed against production duplicates that Gemini's OCR reads the
    same physical vendor name differently across separate resends (e.g. the
    same receipt read as four different vendor-name spellings across four
    sends), so an exact vendor match let those duplicates through."""
    from datetime import date
    from unittest.mock import patch
    from app.agent.tools import exec_save_invoice
    from app.db.models import Invoice
    from tests.conftest import make_invoice

    make_invoice(
        db, group_id="123@g.us", vendor="קולבולית", amount_original=Decimal("30"),
        currency_original="ILS", invoice_date=date(2026, 8, 8),
    )

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)):
        result = await exec_save_invoice(
            group_id="123@g.us", is_admin=True,
            vendor="כורל בולית עין חרוד איחוד", amount=30.0, currency="ILS", date="2026-08-08",
        )

    assert result.get("duplicate") is True
    assert db.query(Invoice).filter_by(group_id="123@g.us").count() == 1


@pytest.mark.asyncio
async def test_exec_save_invoice_allows_same_vendor_amount_different_date(db):
    from datetime import date
    from unittest.mock import patch
    from app.agent.tools import exec_save_invoice
    from app.db.models import Invoice
    from tests.conftest import make_invoice

    make_invoice(
        db, group_id="123@g.us", vendor="Acme", amount_original=Decimal("30"),
        currency_original="ILS", invoice_date=date(2026, 8, 1),
    )

    with patch("app.agent.tools.SessionLocal", return_value=SessionCM(db)), \
         patch("app.pipeline.dedup.SessionLocal", return_value=SessionCM(db)):
        result = await exec_save_invoice(
            group_id="123@g.us", is_admin=True,
            vendor="Acme", amount=30.0, currency="ILS", date="2026-08-08",
        )

    assert "duplicate" not in result
    assert db.query(Invoice).filter_by(group_id="123@g.us", vendor="Acme").count() == 2
