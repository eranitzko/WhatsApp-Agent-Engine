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
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"description": "Work invoices only. USD."}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.httpx.AsyncClient", return_value=mock_client):
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

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"description": ""}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.httpx.AsyncClient", return_value=mock_client):
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

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.httpx.AsyncClient", return_value=mock_client):
        reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")

    assert "failed" in reply.lower() or "connection" in reply.lower()
    db.expire_all()
    entry = db.get(GroupRegistry, "123@g.us")
    assert entry.custom_instructions is None  # not modified on error
