from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
import pytest
from app.db.models import GroupRegistry, AdminNumbers, UserAccount, Blueprint
from app.accounting.group_registration import GroupRegistrationHandler


def _seed(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    # Sys-admin and their already-registered group
    db.add(AdminNumbers(phone_number="972500", label="admin"))
    db.add(GroupRegistry(group_jid="admin_g@g.us", blueprint_id="family_accounting", group_type="sys_admin"))
    db.add(UserAccount(phone="972500", group_jid="admin_g@g.us", role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_bot_joins_admin_group_registers_immediately(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    db.add(AdminNumbers(phone_number="972500", label="admin"))
    db.commit()

    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await handler.on_bot_added_to_group(
            db=db,
            group_jid="new_admin_g@g.us",
            human_phones=["972500"],
        )

    grp = db.query(GroupRegistry).filter_by(group_jid="new_admin_g@g.us").first()
    assert grp is not None
    assert grp.group_type == "sys_admin"
    acct = db.query(UserAccount).filter_by(phone="972500", group_jid="new_admin_g@g.us").first()
    assert acct is not None
    mock_bc.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_joins_unknown_user_group_notifies_admins(db):
    _seed(db)
    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await handler.on_bot_added_to_group(
            db=db,
            group_jid="eden_g@g.us",
            human_phones=["972501"],
        )

    grp = db.query(GroupRegistry).filter_by(group_jid="eden_g@g.us").first()
    assert grp is not None
    assert grp.group_type == "unregistered"
    # Sys-admin notified
    mock_bc.send_message.assert_awaited_once()
    call_args = mock_bc.send_message.call_args[0]
    assert "eden_g@g.us" in call_args[1] or "972501" in call_args[1]


@pytest.mark.asyncio
async def test_approve_registration_registers_group(db):
    _seed(db)
    # Unregistered group exists
    db.add(GroupRegistry(group_jid="eden_g@g.us", blueprint_id="family_accounting", group_type="unregistered"))
    db.commit()

    handler = GroupRegistrationHandler()
    # Simulate a pending registration
    handler._pending["eden_g@g.us"] = {
        "human_phones": ["972501"],
        "group_type": "personal",
        "sys_admin_jids": ["admin_g@g.us"],
        "created_at": datetime.now(timezone.utc),
    }

    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        handled = await handler.handle_admin_reply(
            db=db,
            admin_group_jid="admin_g@g.us",
            reply="yes",
        )

    assert handled is True
    grp = db.query(GroupRegistry).filter_by(group_jid="eden_g@g.us").first()
    assert grp.group_type == "personal"
    acct = db.query(UserAccount).filter_by(phone="972501").first()
    assert acct is not None


@pytest.mark.asyncio
async def test_approve_returns_false_when_no_pending(db):
    _seed(db)
    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client"):
        handled = await handler.handle_admin_reply(
            db=db,
            admin_group_jid="admin_g@g.us",
            reply="yes",
        )
    assert handled is False


def test_is_pending_reply_recognizes_confirmation_word_used_elsewhere(db):
    """Regression: this check must recognize every word the cross-group
    confirmation intercept (app/main.py) recognizes — previously it used
    a narrower word list missing 'אישור'/'ביטול', so a sys_admin approving a
    registration with 'אישור' silently fell through instead of approving."""
    _seed(db)
    handler = GroupRegistrationHandler()
    handler._pending["eden_g@g.us"] = {
        "human_phones": ["972501"],
        "group_type": "personal",
        "sys_admin_jids": ["admin_g@g.us"],
        "created_at": datetime.now(timezone.utc),
    }

    assert handler.is_pending_reply(db, "admin_g@g.us", "אישור") is True
    assert handler.is_pending_reply(db, "admin_g@g.us", "ביטול") is True


def test_get_pending_description_describes_pending_registration(db):
    """Lets main.py's AI-classification fallback give a free-form reply (one
    that doesn't exact-match is_pending_reply's word list) context to judge
    against, instead of the admin's registration approval being silently
    unresolvable the way an exact-match-only check leaves it."""
    _seed(db)
    handler = GroupRegistrationHandler()
    handler._pending["eden_g@g.us"] = {
        "human_phones": ["972501"],
        "group_type": "personal",
        "sys_admin_jids": ["admin_g@g.us"],
        "created_at": datetime.now(timezone.utc),
    }

    desc = handler.get_pending_description("admin_g@g.us")
    assert desc is not None
    assert "eden_g@g.us" in desc
    assert "972501" in desc
    assert "personal" in desc


def test_get_pending_description_returns_none_when_nothing_pending(db):
    handler = GroupRegistrationHandler()
    assert handler.get_pending_description("admin_g@g.us") is None
