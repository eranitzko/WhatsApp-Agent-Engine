import inspect
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.db.models import AutomationRule, GroupRegistry, Blueprint


# ── ORM tests ─────────────────────────────────────────────────────────────────

def _seed_group_for_orm(db):
    db.add(Blueprint(
        id="invoice_curator",
        display_name="Invoice Curator",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()


def test_automation_rule_model_has_required_columns(db):
    _seed_group_for_orm(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="Friday debt reminder",
        rule_type="recurring",
        schedule_cron="0 9 * * 5",
        action_type="send_message",
        action_config=json.dumps({"message": "Please settle debts!"}),
    )
    db.add(rule)
    db.commit()
    db.expire_all()
    fetched = db.get(AutomationRule, rule.id)
    assert fetched.name == "Friday debt reminder"
    assert fetched.rule_type == "recurring"
    assert fetched.status == "pending_confirm"
    assert fetched.last_fired_at is None


def test_automation_rule_defaults_status_to_pending_confirm(db):
    _seed_group_for_orm(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="inactivity",
        inactivity_hours=48,
        action_type="send_message",
        action_config=json.dumps({"message": "hello"}),
    )
    db.add(rule)
    db.commit()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "pending_confirm"


# ── Executor tests ────────────────────────────────────────────────────────────────

from unittest.mock import AsyncMock, MagicMock, patch

from app.automation.executor import AutomationExecutor


def _make_rule(action_type: str, action_config: dict, rule_type="recurring") -> AutomationRule:
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test rule",
        rule_type=rule_type,
        action_type=action_type,
        action_config=json.dumps(action_config),
    )
    rule.id = "rule-1"
    return rule


@pytest.mark.asyncio
async def test_executor_send_message_posts_to_bridge(db):
    executor = AutomationExecutor()
    rule = _make_rule("send_message", {"message": "hello group"})

    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.bridge_client.httpx.AsyncClient", return_value=mock_client):
        await executor.execute(rule, db)

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.kwargs["json"]["text"] == "hello group"
    assert call_kwargs.kwargs["json"]["jid"] == "123@g.us"


@pytest.mark.asyncio
async def test_executor_send_message_with_mentions(db):
    executor = AutomationExecutor()
    rule = _make_rule("send_message", {"message": "pay up", "mentions": ["972500000001"]})

    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.bridge_client.httpx.AsyncClient", return_value=mock_client):
        await executor.execute(rule, db)

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["mentions"] == ["972500000001"]


@pytest.mark.asyncio
async def test_executor_run_agent_action_calls_registry_tool(db):
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = True
    mock_registry.execute = AsyncMock(return_value="ok")

    executor = AutomationExecutor()
    rule = _make_rule("run_agent_action", {"action": "get_balance", "phone": "972501234567"})

    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)

    mock_registry.execute.assert_called_once_with(
        "get_balance", {"phone": "972501234567"},
        group_jid="123@g.us", is_admin=True, sender="",
    )


@pytest.mark.asyncio
async def test_executor_unknown_action_sends_error_message(db):
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = False
    mock_send = AsyncMock()

    executor = AutomationExecutor()
    rule = _make_rule("run_agent_action", {"action": "nonexistent_tool"})

    with patch("app.automation.executor.registry_ref") as mock_ref, \
         patch("app.automation.executor.send_message", mock_send):
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)

    mock_send.assert_called_once()
    msg = mock_send.call_args.args[1]
    assert "nonexistent_tool" in msg
    assert "administrator" in msg.lower()


@pytest.mark.asyncio
async def test_executor_tool_error_does_not_raise(db):
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = True
    mock_registry.execute = AsyncMock(side_effect=RuntimeError("boom"))

    executor = AutomationExecutor()
    rule = _make_rule("run_agent_action", {"action": "get_balance"})

    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)  # must not raise


# ── Tool tests ────────────────────────────────────────────────────────────────

from app.tools.automation_tools import get_automation_tools


class _CM:
    """Wrap a SQLAlchemy Session as a context manager for patching SessionLocal."""
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


def _seed_group(db):
    """Seed a GroupRegistry row so FK constraints are satisfied."""
    db.add(Blueprint(
        id="family_accounting",
        display_name="Family Accounting",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="family_accounting"))
    db.commit()


def test_get_automation_tools_returns_five_tools():
    tools = get_automation_tools()
    assert set(tools.keys()) == {
        "create_automation", "confirm_automation",
        "list_automations", "pause_automation", "cancel_automation",
    }


def test_each_tool_has_schema_and_async_executor():
    tools = get_automation_tools()
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name
        assert inspect.iscoroutinefunction(entry["executor"]), f"{name} executor not async"


@pytest.mark.asyncio
async def test_create_automation_saves_pending_rule(db):
    _seed_group(db)
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["create_automation"]["executor"](
            {
                "name": "Friday debt reminder",
                "rule_type": "recurring",
                "schedule_cron": "0 9 * * 5",
                "action_type": "send_message",
                "action_config": {"message": "Please settle debts!"},
            },
            group_jid="123@g.us",
        )
    assert "Friday debt reminder" in result
    rule = db.query(AutomationRule).filter_by(group_jid="123@g.us").first()
    assert rule is not None
    assert rule.status == "pending_confirm"
    assert rule.schedule_cron == "0 9 * * 5"


@pytest.mark.asyncio
async def test_confirm_automation_activates_rule(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message",
        action_config=json.dumps({"message": "hi"}),
        status="pending_confirm",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["confirm_automation"]["executor"](
            {"id": rule_id},
            group_jid="123@g.us",
        )
    assert "active" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id).status == "active"


@pytest.mark.asyncio
async def test_confirm_automation_wrong_group_rejected(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message",
        action_config=json.dumps({"message": "hi"}),
        status="pending_confirm",
    )
    db.add(rule)
    db.commit()

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["confirm_automation"]["executor"](
            {"id": rule.id},
            group_jid="999@g.us",
        )
    assert "different group" in result.lower()


@pytest.mark.asyncio
async def test_list_automations_returns_active_and_paused(db):
    _seed_group(db)
    for name, status in [("rule-a", "active"), ("rule-b", "paused"), ("rule-c", "done")]:
        db.add(AutomationRule(
            group_jid="123@g.us", name=name, rule_type="recurring",
            schedule_cron="0 9 * * 1",
            action_type="send_message", action_config=json.dumps({"message": "x"}),
            status=status,
        ))
    db.commit()

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["list_automations"]["executor"]({}, group_jid="123@g.us")
    assert "rule-a" in result
    assert "rule-b" in result
    assert "rule-c" not in result  # done rules not shown


@pytest.mark.asyncio
async def test_list_automations_empty_group(db):
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["list_automations"]["executor"]({}, group_jid="empty@g.us")
    assert "no" in result.lower()


@pytest.mark.asyncio
async def test_pause_automation(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["pause_automation"]["executor"]({"id": rule_id}, group_jid="123@g.us")
    assert "paused" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id).status == "paused"


@pytest.mark.asyncio
async def test_cancel_automation_deletes_rule(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["cancel_automation"]["executor"]({"id": rule_id}, group_jid="123@g.us")
    assert "deleted" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id) is None


@pytest.mark.asyncio
async def test_create_automation_invalid_rule_type(db):
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["create_automation"]["executor"](
            {
                "name": "bad",
                "rule_type": "nonsense",
                "action_type": "send_message",
                "action_config": {"message": "hi"},
            },
            group_jid="123@g.us",
        )
    assert "invalid" in result.lower()


@pytest.mark.asyncio
async def test_pause_automation_wrong_group_rejected(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["pause_automation"]["executor"](
            {"id": rule.id},
            group_jid="999@g.us",
        )
    assert "different group" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "active"  # unchanged


@pytest.mark.asyncio
async def test_cancel_automation_wrong_group_rejected(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["cancel_automation"]["executor"](
            {"id": rule_id},
            group_jid="999@g.us",
        )
    assert "different group" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id) is not None  # rule still exists


# ── workflow executor tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_executes_steps_in_order(db):
    """Each step is called via ToolRegistry in sequence."""
    from unittest.mock import AsyncMock, MagicMock

    call_order = []

    async def fake_execute(tool_name, params, **ctx):
        call_order.append(tool_name)
        return "ok"

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = True
    mock_registry.execute = AsyncMock(side_effect=fake_execute)

    rule = _make_rule(
        "workflow",
        {
            "steps": [
                {"tool": "export_report", "params": {"format": "pdf", "delivery": "group"}},
                {"tool": "export_report", "params": {"format": "pdf", "delivery": "email"}},
            ]
        },
    )

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)

    assert call_order == ["export_report", "export_report"]


@pytest.mark.asyncio
async def test_workflow_passes_confirmation_store_in_ctx(db):
    """confirmation_store is injected into ctx so request_confirmation works."""
    from unittest.mock import AsyncMock, MagicMock

    received_ctx = {}

    async def capture_ctx(tool_name, params, **ctx):
        received_ctx.update(ctx)
        return "ok"

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = True
    mock_registry.execute = AsyncMock(side_effect=capture_ctx)

    rule = _make_rule(
        "workflow",
        {"steps": [{"tool": "export_report", "params": {}}]},
    )

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)

    assert "confirmation_store" in received_ctx


@pytest.mark.asyncio
async def test_workflow_sends_error_for_unknown_tool(db):
    """If a step tool is not in registry, group gets an error message."""
    from unittest.mock import AsyncMock, MagicMock

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = False
    mock_send = AsyncMock()

    rule = _make_rule(
        "workflow",
        {"steps": [{"tool": "nonexistent_tool", "params": {}}]},
    )

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref, \
         patch("app.automation.executor.send_message", mock_send):
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)

    mock_send.assert_called_once()
    assert "nonexistent_tool" in mock_send.call_args.args[1]
    assert "administrator" in mock_send.call_args.args[1].lower()


@pytest.mark.asyncio
async def test_workflow_stops_at_failed_step(db):
    """Exception in a step stops the workflow; remaining steps are not called."""
    from unittest.mock import AsyncMock, MagicMock

    call_count = 0

    async def failing_execute(tool_name, params, **ctx):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("step failed")

    mock_registry = MagicMock()
    mock_registry.has_tool.return_value = True
    mock_registry.execute = AsyncMock(side_effect=failing_execute)

    rule = _make_rule(
        "workflow",
        {
            "steps": [
                {"tool": "export_report", "params": {}},
                {"tool": "export_report", "params": {}},
            ]
        },
    )

    executor = AutomationExecutor()
    with patch("app.automation.executor.registry_ref") as mock_ref:
        mock_ref.get_registry.return_value = mock_registry
        await executor.execute(rule, db)  # must not raise

    assert call_count == 1  # stopped after first failure
