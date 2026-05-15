import pytest
from app.db.models import Blueprint, GroupRegistry
from app.router import Router

BLUEPRINT_DEFAULTS = dict(
    display_name="Test Bot",
    system_prompt="You are helpful.",
    model="claude-sonnet-4-6",
    tools_enabled="[]",
    max_tool_turns=3,
    context_window=4,
    context_idle_reset_minutes=30,
)

@pytest.fixture
def seeded_db(db):
    db.add(Blueprint(id="test_bot", **BLUEPRINT_DEFAULTS))
    db.add(GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="test_bot",
        status="active",
        trigger_type="always",
    ))
    db.commit()
    return db


def test_resolve_known_active_group(seeded_db):
    router = Router()
    blueprint, entry = router.resolve(seeded_db, "123@g.us")
    assert blueprint is not None
    assert blueprint.id == "test_bot"
    assert entry.status == "active"


def test_resolve_unknown_group_returns_none(seeded_db):
    router = Router()
    blueprint, entry = router.resolve(seeded_db, "unknown@g.us")
    assert blueprint is None
    assert entry is None


def test_resolve_paused_group_returns_none(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.status = "paused"
    seeded_db.commit()
    router = Router()
    blueprint, result = router.resolve(seeded_db, "123@g.us")
    assert blueprint is None


def test_trigger_always_accepts_any_message(seeded_db):
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="anything", bot_phone="972501234567") is True


def test_trigger_mention_accepts_when_bot_mentioned(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "mention"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hey @972501234567 what's up", bot_phone="972501234567") is True


def test_trigger_mention_blocks_without_mention(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "mention"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hello world", bot_phone="972501234567") is False


def test_trigger_prefix_accepts_matching_prefix(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "prefix"
    entry.trigger_prefix = "!bot"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="!bot what time is it", bot_phone="972501234567") is True


def test_trigger_prefix_blocks_without_prefix(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "prefix"
    entry.trigger_prefix = "!bot"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hello world", bot_phone="972501234567") is False
