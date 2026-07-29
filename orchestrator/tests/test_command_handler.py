import json
import pytest
from app.db.models import GroupRegistry, AdminNumbers, ConversationHistory
from app.command_handler import CommandHandler
from tests.conftest import seed_blueprint, seed_group


@pytest.fixture
def seeded_db(db):
    db.add(AdminNumbers(phone_number="972501234567", label="owner"))
    seed_blueprint(
        db, id="notion_assistant",
        display_name="Notion Assistant",
        system_prompt="You are a Notion assistant.",
        model="claude-sonnet-4-6",
        tools_enabled=json.dumps(["search_pages"]),
    )
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
async def test_bind_invalid_trigger_returns_error(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant --trigger badvalue")
    assert "invalid trigger" in result.lower()


@pytest.mark.asyncio
async def test_bind_rebinds_existing_group(seeded_db):
    seed_group(seeded_db, "123@g.us", blueprint_id="notion_assistant",
               status="paused", trigger_type="mention")
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "active"
    assert entry.trigger_type == "always"


@pytest.mark.asyncio
async def test_non_admin_returns_none(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "999999999", "/bind notion_assistant")
    assert result is None


@pytest.mark.asyncio
async def test_unbind_removes_entry(seeded_db):
    seed_group(seeded_db, "123@g.us", blueprint_id="notion_assistant",
               status="active", trigger_type="always")
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/unbind")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry is None


@pytest.mark.asyncio
async def test_pause_sets_paused_status(seeded_db):
    seed_group(seeded_db, "123@g.us", blueprint_id="notion_assistant",
               status="active", trigger_type="always")
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/pause")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "paused"


@pytest.mark.asyncio
async def test_resume_sets_active_status(seeded_db):
    seed_group(seeded_db, "123@g.us", blueprint_id="notion_assistant",
               status="paused", trigger_type="always")
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
    assert handler.is_command("/binding a shelf") is False


def test_command_handler_admin_check_uses_canonical_phone(db):
    """/bind and friends must be checked against the canonical phone, not a
    raw LID — this test documents the contract command_handler.handle relies
    on; the actual fix (passing the resolved phone) lives in main.py's
    _process, where resolve_inbound must run before the command_handler
    dispatch, not after."""
    from app.db.models import AdminNumbers

    db.add(AdminNumbers(phone_number="972523206175"))
    db.commit()

    handler = CommandHandler()
    # Canonical phone (post-resolution) is recognized as admin
    assert handler._is_admin(db, "972523206175") is True
    # Raw LID (pre-resolution) is NOT recognized — proving why main.py must
    # resolve before calling handle(), not pass the raw sender split.
    assert handler._is_admin(db, "175715853041683") is False
