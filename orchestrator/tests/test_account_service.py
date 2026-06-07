from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from app.db.models import (
    UserAccount, GroupRegistry, AdminNumbers, UserProfile, Blueprint,
)
from app.accounting.account_service import AccountService


def _seed_blueprint(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    db.commit()


def _seed_group(db, jid: str, group_type: str = "personal") -> GroupRegistry:
    _seed_blueprint(db)
    g = GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type=group_type)
    db.add(g)
    db.commit()
    return g


def _seed_user(db, phone: str, group_jid: str, role: str = "owner") -> UserAccount:
    u = UserAccount(phone=phone, group_jid=group_jid, role=role)
    db.add(u)
    db.commit()
    return u


def test_resolve_user_returns_account(db):
    _seed_group(db, "grp1@g.us")
    _seed_user(db, "972501", "grp1@g.us")
    svc = AccountService()
    acct = svc.resolve_user(db, "972501")
    assert acct is not None
    assert acct.phone == "972501"


def test_resolve_user_returns_none_for_unknown(db):
    svc = AccountService()
    assert svc.resolve_user(db, "999999") is None


def test_resolve_group_owner(db):
    _seed_group(db, "grp2@g.us")
    _seed_user(db, "972502", "grp2@g.us", role="owner")
    svc = AccountService()
    assert svc.resolve_group_owner(db, "grp2@g.us") == "972502"


def test_resolve_group_owner_returns_none_when_no_owner(db):
    _seed_group(db, "grp3@g.us")
    svc = AccountService()
    assert svc.resolve_group_owner(db, "grp3@g.us") is None


def test_get_group_members_returns_all_phones(db):
    _seed_group(db, "grp4@g.us", group_type="shared")
    _seed_user(db, "972503", "grp4@g.us", role="member")
    _seed_user(db, "972504", "grp4@g.us", role="member")
    svc = AccountService()
    members = svc.get_group_members(db, "grp4@g.us")
    assert set(members) == {"972503", "972504"}


def test_get_display_name_uses_display_name_if_set(db):
    p = UserProfile(phone="972505", display_name="Eran")
    db.add(p)
    db.commit()
    svc = AccountService()
    assert svc.get_display_name(db, "972505") == "Eran"


def test_get_display_name_falls_back_to_phone(db):
    svc = AccountService()
    assert svc.get_display_name(db, "972506") == "972506"


def test_is_sys_admin_true(db):
    db.add(AdminNumbers(phone_number="972507"))
    db.commit()
    svc = AccountService()
    assert svc.is_sys_admin(db, "972507") is True


def test_is_sys_admin_false(db):
    svc = AccountService()
    assert svc.is_sys_admin(db, "999999") is False


def test_get_group_type(db):
    _seed_group(db, "grp5@g.us", group_type="sys_admin")
    svc = AccountService()
    assert svc.get_group_type(db, "grp5@g.us") == "sys_admin"


def test_get_group_type_unknown_returns_unregistered(db):
    svc = AccountService()
    assert svc.get_group_type(db, "unknown@g.us") == "unregistered"


import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
from app.db.models import CrossGroupConfirmation


@pytest.mark.asyncio
async def test_notify_user_sends_to_personal_group(db):
    _seed_group(db, "eden_grp@g.us")
    _seed_user(db, "972510", "eden_grp@g.us")
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.notify_user(db, "972510", "Hello")
    mock_bc.send_message.assert_awaited_once_with("eden_grp@g.us", "Hello")


@pytest.mark.asyncio
async def test_notify_user_silent_when_no_group(db):
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.notify_user(db, "999999", "Hello")
    mock_bc.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_confirmation_creates_row(db):
    _seed_group(db, "tal_grp@g.us")
    _seed_user(db, "972511", "tal_grp@g.us")
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        conf = await svc.request_confirmation(
            db=db,
            initiator_phone="972500",
            initiator_group_jid="eden_grp@g.us",
            target_phone="972511",
            action_type="record_expense",
            action_payload={"amount_ils": "100.00"},
            confirmation_message="Tal, Eden says you owe ₪100. Confirm?",
        )
    assert conf.id is not None
    assert conf.status == "pending"
    assert conf.target_phone == "972511"
    assert conf.target_group_jid == "tal_grp@g.us"
    mock_bc.send_message.assert_awaited_once_with(
        "tal_grp@g.us",
        "Tal, Eden says you owe ₪100. Confirm?",
    )


def test_handle_confirmation_reply_yes_flips_status(db):
    _seed_group(db, "tal_grp2@g.us")
    _seed_user(db, "972512", "tal_grp2@g.us")
    now = datetime.now(timezone.utc)
    conf = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972512",
        target_group_jid="tal_grp2@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "50.00"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    resolved = svc.handle_confirmation_reply(db, "tal_grp2@g.us", "972512", "yes")
    assert resolved is True
    db.refresh(conf)
    assert conf.status == "confirmed"


def test_handle_confirmation_reply_no_flips_status(db):
    _seed_group(db, "tal_grp3@g.us")
    _seed_user(db, "972513", "tal_grp3@g.us")
    now = datetime.now(timezone.utc)
    conf = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972513",
        target_group_jid="tal_grp3@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "50.00"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    resolved = svc.handle_confirmation_reply(db, "tal_grp3@g.us", "972513", "no")
    assert resolved is True
    db.refresh(conf)
    assert conf.status == "rejected"


def test_handle_confirmation_reply_returns_false_when_no_pending(db):
    svc = AccountService()
    result = svc.handle_confirmation_reply(db, "grp@g.us", "972500", "yes")
    assert result is False
