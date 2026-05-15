import json
import pytest
from app.db.models import Blueprint, GroupRegistry, AdminNumbers

def test_blueprint_tools_list_parses_json(db):
    bp = Blueprint(
        id="test",
        display_name="Test",
        system_prompt="You are helpful.",
        model="claude-sonnet-4-6",
        tools_enabled=json.dumps(["tool_a", "tool_b"]),
    )
    db.add(bp)
    db.commit()
    fetched = db.query(Blueprint).filter_by(id="test").first()
    assert fetched.tools_list() == ["tool_a", "tool_b"]

def test_group_registry_references_blueprint(db):
    db.add(Blueprint(
        id="bot",
        display_name="Bot",
        system_prompt="...",
        model="claude-sonnet-4-6",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="bot",
        status="active",
        trigger_type="always",
    ))
    db.commit()
    entry = db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.blueprint_id == "bot"
    assert entry.status == "active"

def test_admin_numbers_stores_phone(db):
    db.add(AdminNumbers(phone_number="972501234567", label="owner"))
    db.commit()
    admin = db.query(AdminNumbers).filter_by(phone_number="972501234567").first()
    assert admin is not None
    assert admin.label == "owner"
