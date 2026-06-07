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
