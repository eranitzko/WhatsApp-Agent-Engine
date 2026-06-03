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
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.automation.executor.httpx.AsyncClient", return_value=mock_client):
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
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.automation.executor.httpx.AsyncClient", return_value=mock_client):
        await executor.execute(rule, db)

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["mentions"] == ["972500000001"]


@pytest.mark.asyncio
async def test_executor_run_agent_action_calls_registered_fn(db):
    called_with = {}

    async def fake_action(group_jid, db, config):
        called_with["group_jid"] = group_jid
        called_with["config"] = config

    executor = AutomationExecutor(actions={"balance_summary": fake_action})
    rule = _make_rule("run_agent_action", {"action": "balance_summary"})
    await executor.execute(rule, db)

    assert called_with["group_jid"] == "123@g.us"
    assert called_with["config"]["action"] == "balance_summary"


@pytest.mark.asyncio
async def test_executor_unknown_action_logs_and_does_not_raise(db):
    executor = AutomationExecutor()
    rule = _make_rule("run_agent_action", {"action": "nonexistent"})
    # Should not raise
    await executor.execute(rule, db)


@pytest.mark.asyncio
async def test_executor_error_in_action_does_not_raise(db):
    async def bad_action(group_jid, db, config):
        raise RuntimeError("boom")

    executor = AutomationExecutor(actions={"bad": bad_action})
    rule = _make_rule("run_agent_action", {"action": "bad"})
    # Should swallow the exception
    await executor.execute(rule, db)
