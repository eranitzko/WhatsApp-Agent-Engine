import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.command_handler import CommandHandler
from app.db.models import GroupRegistry, AdminNumbers, Blueprint


def _seed(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    db.add(Blueprint(
        id="invoice_curator",
        display_name="Invoice Curator",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()


@pytest.mark.asyncio
async def test_sync_stores_description(db):
    _seed(db)
    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(return_value={
        "description": "Work invoices only. USD.",
        "participants": [],
    })):
        reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")

    assert "synced" in reply.lower()
    db.expire_all()
    entry = db.get(GroupRegistry, "123@g.us")
    assert entry.custom_instructions == "Work invoices only. USD."


@pytest.mark.asyncio
async def test_sync_clears_instructions_when_description_empty(db):
    _seed(db)
    db.get(GroupRegistry, "123@g.us").custom_instructions = "old value"
    db.commit()

    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(return_value={
        "description": "",
        "participants": [],
    })):
        await handler.handle(db, "123@g.us", "972500000001", "/sync")

    db.expire_all()
    entry = db.get(GroupRegistry, "123@g.us")
    assert entry.custom_instructions is None


@pytest.mark.asyncio
async def test_sync_no_group_bound(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()

    handler = CommandHandler(bridge_url="http://bridge:3000")
    reply = await handler.handle(db, "999@g.us", "972500000001", "/sync")
    assert "no agent" in reply.lower()


def test_group_registry_has_custom_instructions_column(db):
    entry = GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="invoice_curator",
        custom_instructions="Work invoices only. USD.",
    )
    db.add(entry)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupRegistry, "123@g.us")
    assert fetched.custom_instructions == "Work invoices only. USD."


def test_custom_instructions_defaults_to_none(db):
    entry = GroupRegistry(group_jid="456@g.us", blueprint_id="invoice_curator")
    db.add(entry)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupRegistry, "456@g.us")
    assert fetched.custom_instructions is None


@pytest.mark.asyncio
async def test_sync_no_bridge_url(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    db.add(Blueprint(
        id="invoice_curator",
        display_name="Invoice Curator",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()

    handler = CommandHandler(bridge_url="")
    reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")
    assert "bridge url" in reply.lower() or "not configured" in reply.lower()


@pytest.mark.asyncio
async def test_sync_bridge_http_error(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    db.add(Blueprint(
        id="invoice_curator",
        display_name="Invoice Curator",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()

    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(side_effect=Exception("connection refused"))):
        reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")

    assert "failed" in reply.lower() or "connection" in reply.lower()
    db.expire_all()
    entry = db.get(GroupRegistry, "123@g.us")
    assert entry.custom_instructions is None  # not modified on error


import anthropic
from app.agent_runner import AgentRunner
from app.tool_registry import ToolRegistry


def _make_runner():
    client = MagicMock()
    registry = ToolRegistry()
    return AgentRunner(client, registry)


def _make_blueprint():
    bp = MagicMock()
    bp.system_prompt = "Base prompt."
    bp.model = "claude-sonnet-4-6"
    bp.max_tool_turns = 1
    bp.context_window = 4
    bp.context_idle_reset_minutes = 60
    bp.tools_list.return_value = []
    return bp


def _fake_end_turn(text="ok"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_custom_instructions_appended_to_system_prompt():
    runner = _make_runner()
    bp = _make_blueprint()

    captured = {}

    async def fake_create(**kwargs):
        captured["system"] = kwargs["system"]
        return _fake_end_turn()

    runner.client.messages.create = fake_create

    context = MagicMock()
    context.get_history.return_value = []
    context.add = MagicMock()
    confirmation_store = MagicMock()
    confirmation_store.get.return_value = None

    await runner.run(
        blueprint=bp,
        group_jid="123@g.us",
        sender="972500000001@s.whatsapp.net",
        is_admin=False,
        message="hello",
        context=context,
        confirmation_store=confirmation_store,
        custom_instructions="Work invoices only. USD.",
    )

    system_texts = [block["text"] for block in captured["system"]]
    assert any("Work invoices only" in t for t in system_texts)


@pytest.mark.asyncio
async def test_no_custom_instructions_block_when_none():
    runner = _make_runner()
    bp = _make_blueprint()

    captured = {}

    async def fake_create(**kwargs):
        captured["system"] = kwargs["system"]
        return _fake_end_turn()

    runner.client.messages.create = fake_create

    context = MagicMock()
    context.get_history.return_value = []
    context.add = MagicMock()
    confirmation_store = MagicMock()
    confirmation_store.get.return_value = None

    await runner.run(
        blueprint=bp,
        group_jid="123@g.us",
        sender="972500000001@s.whatsapp.net",
        is_admin=False,
        message="hello",
        context=context,
        confirmation_store=confirmation_store,
        custom_instructions=None,
    )

    system_texts = [block["text"] for block in captured["system"]]
    assert not any("Group-specific" in t for t in system_texts)
