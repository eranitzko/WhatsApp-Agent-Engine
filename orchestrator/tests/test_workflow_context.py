"""Tests for WorkflowContext — template resolution and built-in variables."""

import calendar
import pytest
from datetime import date
from unittest.mock import MagicMock, patch


# ── Template resolution ───────────────────────────────────────────────────────

def test_resolve_plain_string_unchanged():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    assert ctx.resolve("hello world") == "hello world"


def test_resolve_known_variable():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {"name": "Alice"})
    assert ctx.resolve("Hello {{name}}") == "Hello Alice"


def test_resolve_unknown_variable_left_as_is():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    assert ctx.resolve("Total: {{unknown_var}}") == "Total: {{unknown_var}}"


def test_resolve_multiple_variables():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {"a": "foo", "b": "bar"})
    assert ctx.resolve("{{a}} and {{b}}") == "foo and bar"


def test_resolve_dict_resolves_string_values():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {"month": "May 2026"})
    result = ctx.resolve_dict({"subject": "Report for {{month}}", "count": 5})
    assert result == {"subject": "Report for May 2026", "count": 5}


def test_resolve_dict_leaves_non_strings_unchanged():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    result = ctx.resolve_dict({"nested": {"key": "val"}, "num": 42})
    assert result["num"] == 42
    assert result["nested"] == {"key": "val"}


# ── Built-in variables ────────────────────────────────────────────────────────

def test_builtin_group_jid():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("abc@g.us", {})
    assert ctx.resolve("{{group_jid}}") == "abc@g.us"


def test_builtin_today():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    assert ctx.resolve("{{today}}") == date.today().isoformat()


def test_builtin_previous_month_name():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    result = ctx.resolve("{{previous_month_name}}")
    assert result in list(calendar.month_name)[1:]


def test_builtin_previous_month_year():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    result = ctx.resolve("{{previous_month_year}}")
    assert result.isdigit() and len(result) == 4


def test_builtin_previous_month():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    result = ctx.resolve("{{previous_month}}")
    parts = result.split(" ")
    assert len(parts) == 2 and parts[1].isdigit()


def test_builtin_current_month():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    result = ctx.resolve("{{current_month}}")
    parts = result.split(" ")
    assert len(parts) == 2 and parts[0] in list(calendar.month_name)[1:]


# ── Step output storage ───────────────────────────────────────────────────────

def test_store_and_retrieve_step_output():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    ctx.store("total", "1,234.50")
    assert ctx.resolve("Amount: {{total}} ILS") == "Amount: 1,234.50 ILS"


def test_store_overwrites_existing_key():
    from app.automation.context import WorkflowContext
    ctx = WorkflowContext("123@g.us", {})
    ctx.store("x", "first")
    ctx.store("x", "second")
    assert ctx.resolve("{{x}}") == "second"


# ── DB metric variables ───────────────────────────────────────────────────────

def test_db_metric_monthly_invoice_total_resolves(db):
    from app.automation.context import WorkflowContext
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = 1500.75
    with patch("app.automation.context.ThresholdEvaluator", return_value=mock_evaluator):
        ctx = WorkflowContext("123@g.us", {}, db=db)
        result = ctx.resolve("Total: {{monthly_invoice_total}}")
    assert result == "Total: 1,500.75"


def test_db_metric_open_debt_amount_resolves(db):
    from app.automation.context import WorkflowContext
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = 300.0
    with patch("app.automation.context.ThresholdEvaluator", return_value=mock_evaluator):
        ctx = WorkflowContext("123@g.us", {}, db=db)
        result = ctx.resolve("Debt: {{open_debt_amount}}")
    assert result == "Debt: 300.00"


def test_db_metric_infinity_formatted(db):
    from app.automation.context import WorkflowContext
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = float("inf")
    with patch("app.automation.context.ThresholdEvaluator", return_value=mock_evaluator):
        ctx = WorkflowContext("123@g.us", {}, db=db)
        result = ctx.resolve("{{days_since_last_settlement}}")
    assert result == "∞"


def test_db_metric_integer_formatted(db):
    from app.automation.context import WorkflowContext
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = 7.0
    with patch("app.automation.context.ThresholdEvaluator", return_value=mock_evaluator):
        ctx = WorkflowContext("123@g.us", {}, db=db)
        result = ctx.resolve("{{invoice_count_this_month}}")
    assert result == "7"


# ── Executor integration ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_resolves_templates_in_params(db):
    """Params containing {{variables}} are resolved before tool execution."""
    from unittest.mock import AsyncMock, MagicMock
    from app.automation.executor import AutomationExecutor
    import json

    received_params = {}

    async def capture(tool_name, params, **ctx):
        received_params.update(params)
        return "ok"

    mock_reg = MagicMock()
    mock_reg.has_tool.return_value = True
    mock_reg.execute = AsyncMock(side_effect=capture)

    from app.db.models import AutomationRule
    rule = AutomationRule(
        group_jid="123@g.us", name="t", rule_type="recurring",
        action_type="workflow",
        action_config=json.dumps({"steps": [
            {"tool": "send_email", "params": {"body": "Month: {{previous_month}}", "to": "a@b.com", "subject": "x"}}
        ]}),
    )
    rule.id = "r1"

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_reg
        await executor.execute(rule, db)

    # previous_month should have been resolved to an actual value
    assert "{{previous_month}}" not in received_params.get("body", "")
    assert len(received_params.get("body", "")) > 5


@pytest.mark.asyncio
async def test_workflow_stores_output_key_for_later_steps(db):
    """output_key stores step result for {{key}} in later steps."""
    from unittest.mock import AsyncMock, MagicMock
    from app.automation.executor import AutomationExecutor
    import json

    step_params = []

    async def capture(tool_name, params, **ctx):
        step_params.append(dict(params))
        return "computed_value_42"

    mock_reg = MagicMock()
    mock_reg.has_tool.return_value = True
    mock_reg.execute = AsyncMock(side_effect=capture)

    from app.db.models import AutomationRule
    rule = AutomationRule(
        group_jid="123@g.us", name="t", rule_type="recurring",
        action_type="workflow",
        action_config=json.dumps({"steps": [
            {"tool": "tool_a", "params": {}, "output_key": "result_a"},
            {"tool": "tool_b", "params": {"message": "Got: {{result_a}}"}},
        ]}),
    )
    rule.id = "r1"

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_reg
        await executor.execute(rule, db)

    assert step_params[1]["message"] == "Got: computed_value_42"


# ── send_email tool ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_sends_with_resolved_templates():
    from unittest.mock import MagicMock, patch, AsyncMock
    from app.tools.send_email_tool import _exec_send_email

    mock_mail = MagicMock()

    with patch("app.tools.send_email_tool.send_report_email", mock_mail), \
         patch("app.tools.send_email_tool.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, **kw: fn(**kw))), \
         patch("app.tools.send_email_tool._is_allowed", return_value=True):
        result = await _exec_send_email(
            {"to": "test@example.com", "subject": "Hello", "body": "World"},
            group_jid="123@g.us", is_admin=True,
        )

    assert "test@example.com" in result
    mock_mail.assert_called_once_with(
        to="test@example.com",
        subject="Hello",
        body="World",
        attachments=[],
    )


@pytest.mark.asyncio
async def test_send_email_rejected_if_not_admin():
    from app.tools.send_email_tool import _exec_send_email
    result = await _exec_send_email(
        {"to": "x@y.com", "subject": "s", "body": "b"},
        group_jid="123@g.us", is_admin=False,
    )
    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_send_email_rejected_if_not_in_allowlist():
    from app.tools.send_email_tool import _exec_send_email
    with patch("app.tools.send_email_tool._is_allowed", return_value=False):
        result = await _exec_send_email(
            {"to": "notallowed@example.com", "subject": "s", "body": "b"},
            group_jid="123@g.us", is_admin=True,
        )
    assert "not" in result.lower() or "allow" in result.lower()


@pytest.mark.asyncio
async def test_send_email_resolves_templates_from_context():
    """Templates in subject/body are resolved via WorkflowContext at send time."""
    from unittest.mock import MagicMock, patch, AsyncMock
    from app.tools.send_email_tool import _exec_send_email

    mock_mail = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.resolve.side_effect = lambda s: s.replace("{{previous_month}}", "May 2026")

    with patch("app.tools.send_email_tool.send_report_email", mock_mail), \
         patch("app.tools.send_email_tool.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, **kw: fn(**kw))), \
         patch("app.tools.send_email_tool.WorkflowContext", return_value=mock_ctx), \
         patch("app.tools.send_email_tool._is_allowed", return_value=True):
        result = await _exec_send_email(
            {"to": "x@example.com", "subject": "{{previous_month}} report", "body": "Month: {{previous_month}}"},
            group_jid="123@g.us", is_admin=True,
        )

    call_kwargs = mock_mail.call_args.kwargs
    assert call_kwargs["subject"] == "May 2026 report"
    assert call_kwargs["body"] == "Month: May 2026"


def test_get_send_email_tools_returns_tool():
    from app.tools.send_email_tool import get_send_email_tools
    tools = get_send_email_tools()
    assert "send_email" in tools
    assert "schema" in tools["send_email"]
    assert "executor" in tools["send_email"]
