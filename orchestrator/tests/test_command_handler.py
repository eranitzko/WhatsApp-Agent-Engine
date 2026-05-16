import json
import pytest
from app.db.models import Blueprint, GroupRegistry, AdminNumbers, ConversationHistory
from app.command_handler import CommandHandler

BLUEPRINT_ROW = Blueprint(
    id="notion_assistant",
    display_name="Notion Assistant",
    system_prompt="You are a Notion assistant.",
    model="claude-sonnet-4-6",
    tools_enabled=json.dumps(["search_pages"]),
)

def _fresh_blueprint():
    return Blueprint(
        id="notion_assistant",
        display_name="Notion Assistant",
        system_prompt="You are a Notion assistant.",
        model="claude-sonnet-4-6",
        tools_enabled=json.dumps(["search_pages"]),
    )

@pytest.fixture
def seeded_db(db):
    db.add(AdminNumbers(phone_number="972501234567", label="owner"))
    db.add(_fresh_blueprint())
    db.commit()
    return db


@pytest.mark.asyncio
async def test_bind_assigns_blueprint(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant")
    assert "Notion Assistant" in result
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry is not None
    assert entry.blueprint_id == "notion_assistant"
    assert entry.status == "active"
    assert entry.trigger_type == "always"


@pytest.mark.asyncio
async def test_bind_with_trigger_mention(seeded_db):
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant --trigger mention")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.trigger_type == "mention"


@pytest.mark.asyncio
async def test_bind_clears_conversation_history(seeded_db):
    seeded_db.add(ConversationHistory(group_id="123@g.us", messages_json="[]"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant")
    history = seeded_db.query(ConversationHistory).filter_by(group_id="123@g.us").first()
    assert history is None


@pytest.mark.asyncio
async def test_bind_unknown_blueprint_returns_error(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind nonexistent")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_non_admin_returns_none(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "999999999", "/bind notion_assistant")
    assert result is None


@pytest.mark.asyncio
async def test_unbind_removes_entry(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="active", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/unbind")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry is None


@pytest.mark.asyncio
async def test_pause_sets_paused_status(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="active", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/pause")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "paused"


@pytest.mark.asyncio
async def test_resume_sets_active_status(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="paused", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/resume")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "active"


@pytest.mark.asyncio
async def test_blueprints_lists_all(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/blueprints")
    assert "notion_assistant" in result
    assert "Notion Assistant" in result


def test_is_command_recognizes_slash_commands():
    handler = CommandHandler()
    assert handler.is_command("/bind notion_assistant") is True
    assert handler.is_command("/unbind") is True
    assert handler.is_command("/pause") is True
    assert handler.is_command("/resume") is True
    assert handler.is_command("/blueprints") is True
    assert handler.is_command("hello world") is False
    assert handler.is_command("") is False
