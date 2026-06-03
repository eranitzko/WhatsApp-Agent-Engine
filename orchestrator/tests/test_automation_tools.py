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
