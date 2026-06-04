"""Tests for the three automation scheduler jobs."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import AutomationRule, ConversationHistory, GroupRegistry, Blueprint
from app.automation.executor import AutomationExecutor


class _CM:
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


def _seed_group(db, group_jid="123@g.us"):
    db.add(Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="p", tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid=group_jid, blueprint_id="family_accounting"))
    db.commit()


def _make_rule(db, rule_type, status="active", **kwargs):
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test rule",
        rule_type=rule_type,
        action_type="send_message",
        action_config=json.dumps({"message": "hello"}),
        status=status,
        **kwargs,
    )
    db.add(rule)
    db.commit()
    return rule


def _mock_executor():
    executor = MagicMock(spec=AutomationExecutor)
    executor.execute = AsyncMock()
    return executor


# ── Recurring rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recurring_rule_fires_when_cron_due(db):
    _seed_group(db)
    # Cron that fired in the last hour: every minute — guaranteed to match
    rule = _make_rule(db, "recurring", schedule_cron="* * * * *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).last_fired_at is not None


@pytest.mark.asyncio
async def test_recurring_rule_does_not_fire_when_not_due(db):
    _seed_group(db)
    # Cron that will not match in the last hour: Feb 30 (impossible date)
    rule = _make_rule(db, "recurring", schedule_cron="0 0 30 2 *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_one_off_rule_fires_and_is_marked_done(db):
    _seed_group(db)
    fire_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    rule = _make_rule(db, "one_off", schedule_cron=fire_at.isoformat())
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "done"


@pytest.mark.asyncio
async def test_one_off_rule_does_not_fire_when_future(db):
    _seed_group(db)
    fire_at = datetime.now(timezone.utc) + timedelta(hours=2)
    rule = _make_rule(db, "one_off", schedule_cron=fire_at.isoformat())
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "active"


@pytest.mark.asyncio
async def test_paused_rule_is_not_fired(db):
    _seed_group(db)
    rule = _make_rule(db, "recurring", status="paused", schedule_cron="* * * * *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()


# ── Inactivity rules ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inactivity_rule_fires_after_long_silence(db):
    _seed_group(db)
    rule = _make_rule(db, "inactivity", inactivity_hours=24)
    # last activity was 30 hours ago
    old_active = datetime.now(timezone.utc) - timedelta(hours=30)
    db.add(ConversationHistory(
        group_id="123@g.us",
        messages_json="[]",
        last_active=old_active,
    ))
    db.commit()
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _check_inactivity
        await _check_inactivity()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).last_fired_at is not None


@pytest.mark.asyncio
async def test_inactivity_rule_does_not_fire_when_recently_active(db):
    _seed_group(db)
    rule = _make_rule(db, "inactivity", inactivity_hours=24)
    # last activity was 1 hour ago — not yet due
    db.add(ConversationHistory(
        group_id="123@g.us",
        messages_json="[]",
        last_active=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db.commit()
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _check_inactivity
        await _check_inactivity()

    executor.execute.assert_not_called()


# ── Threshold rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_threshold_rule_fires_when_condition_met(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 100}),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=500.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_called_once()


@pytest.mark.asyncio
async def test_threshold_rule_does_not_fire_when_condition_not_met(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 1000}),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=50.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_threshold_rule_skips_if_fired_within_24h(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 100}),
        last_fired_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=500.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_not_called()
