"""Tests proving UserProfile routing fields cover members who registered a group
BEFORE being enrolled in a household (the common case for existing members).

If these pass, the LID-safe gap is closed for everyone — not just those who
went through household setup first.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    Blueprint, GroupRegistry, UserAccount, UserProfile,
    CrossGroupConfirmation, Household, HouseholdMember,
)
from app.accounting.account_service import AccountService


# ---------------------------------------------------------------------------
# Seed helpers — deliberately NO HouseholdMember rows
# ---------------------------------------------------------------------------

def _bp(db):
    if db.query(Blueprint).filter_by(id="family_accounting").first() is None:
        db.add(Blueprint(id="family_accounting", display_name="FA",
                         system_prompt="x", model="claude-sonnet-4-6",
                         tools_enabled='[]'))


def _register_personal(db, phone: str, group_jid: str,
                        created_at: datetime | None = None) -> None:
    """Simulate the registration path: UserAccount + UserProfile.private_group_jid.
    Deliberately creates NO HouseholdMember row.
    """
    _bp(db)
    if db.query(GroupRegistry).filter_by(group_jid=group_jid).first() is None:
        db.add(GroupRegistry(group_jid=group_jid, blueprint_id="family_accounting",
                             group_type="personal"))
    acct = UserAccount(phone=phone, group_jid=group_jid, role="owner")
    if created_at:
        acct.created_at = created_at
    db.add(acct)
    # _upsert_profile_private_group equivalent
    profile = db.query(UserProfile).filter_by(phone=phone).first()
    if profile is None:
        db.add(UserProfile(phone=phone, private_group_jid=group_jid))
    elif profile.private_group_jid is None:
        profile.private_group_jid = group_jid


# ---------------------------------------------------------------------------
# Test 1: resolve_inbound uses UserProfile when no HouseholdMember exists.
# ---------------------------------------------------------------------------

def test_resolve_inbound_via_userprofile_no_household_member(db):
    """LID-format sender in a personal group resolves via UserProfile.private_group_jid."""
    _register_personal(db, "972501111111", "eden_priv@g.us")
    db.commit()

    # Confirm no HouseholdMember exists — this is the gap we're closing
    assert db.query(HouseholdMember).filter_by(phone="972501111111").first() is None

    svc = AccountService()
    phone, household_id = svc.resolve_inbound(db, "eden_priv@g.us", "8650248708313:3@lid")

    assert phone == "972501111111", f"Expected canonical phone, got {phone!r}"
    assert household_id is None  # no household yet — acceptable


def test_resolve_inbound_userprofile_returns_household_id_when_later_enrolled(db):
    """After enrollment, resolve_inbound still resolves correctly and now returns household_id."""
    _register_personal(db, "972502222222", "bob_priv@g.us")
    # Now enroll in household
    h = Household(name="Family")
    db.add(h)
    db.flush()
    db.add(HouseholdMember(household_id=h.id, phone="972502222222",
                            private_group_jid="bob_priv@g.us"))
    db.commit()

    svc = AccountService()
    phone, household_id = svc.resolve_inbound(db, "bob_priv@g.us", "8650248708313:3@lid")

    assert phone == "972502222222"
    assert household_id == h.id  # now has household_id from HouseholdMember (strategy 1)


# ---------------------------------------------------------------------------
# Test 2: primary_accounting_group_jid override via UserProfile (no HouseholdMember).
# ---------------------------------------------------------------------------

def test_primary_override_via_userprofile_no_household_member(db):
    """Primary accounting group override works via UserProfile even without HouseholdMember."""
    _register_personal(db, "972503333333", "carol_g1@g.us",
                       created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    _bp(db)
    db.add(GroupRegistry(group_jid="carol_g2@g.us", blueprint_id="family_accounting",
                         group_type="personal"))
    acct2 = UserAccount(phone="972503333333", group_jid="carol_g2@g.us", role="owner")
    acct2.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
    db.add(acct2)

    # Set override on UserProfile — no HouseholdMember
    profile = db.query(UserProfile).filter_by(phone="972503333333").first()
    profile.primary_accounting_group_jid = "carol_g2@g.us"
    db.commit()

    assert db.query(HouseholdMember).filter_by(phone="972503333333").first() is None

    svc = AccountService()
    primary = svc.get_primary_accounting_group(db, "972503333333")
    assert primary == "carol_g2@g.us", f"Expected UserProfile override, got {primary!r}"


# ---------------------------------------------------------------------------
# Test 3: Full end-to-end — member with only UserAccount rows.
#         A says "Carol owes me". Confirm request lands at primary, LID reply resolves.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_to_end_useraccount_only_member(db):
    """Confirm request + LID reply resolves for a member who has no HouseholdMember."""
    _register_personal(db, "972504444444", "carol_acct@g.us")
    _register_personal(db, "972505555555", "alon_acct@g.us")
    db.commit()

    svc = AccountService()
    sent_to: list[str] = []

    async def _capture(jid, msg):
        sent_to.append(jid)

    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = _capture
        result = await svc.process_transaction(
            db=db,
            reporter_phone="972505555555",
            reporter_group_jid="alon_acct@g.us",
            payer_phone="972505555555",
            debtor_phone="972504444444",
            amount_ils=Decimal("150"),
            description="groceries",
            transaction_date=date.today(),
        )

    # Confirmation sent to Carol's group
    assert "carol_acct@g.us" in sent_to, f"Expected carol_acct@g.us, sent to: {sent_to}"

    conf = db.query(CrossGroupConfirmation).filter_by(target_phone="972504444444").first()
    assert conf is not None
    assert conf.target_group_jid == "carol_acct@g.us"

    # Carol replies "yes" with LID sender
    phone, household_id = svc.resolve_inbound(db, "carol_acct@g.us", "9876543210123:1@lid")
    assert phone == "972504444444"

    resolved = svc.handle_confirmation_reply(db, "carol_acct@g.us", phone, "yes",
                                              household_id=household_id)
    assert resolved is not None
    assert resolved.status == "confirmed"


# ---------------------------------------------------------------------------
# Test 4: patch_person PATCH /people/{phone} persists private_group_jid to UserProfile.
# ---------------------------------------------------------------------------

def test_patch_person_sets_private_group_jid_on_userprofile(db):
    """PATCH /people/{phone} with private_group_jid writes to UserProfile."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch as mpatch
    from app.admin.api import router as api_router
    from app.admin.auth import require_auth
    from sqlalchemy.orm import sessionmaker

    _bp(db)
    db.add(GroupRegistry(group_jid="dave_grp@g.us", blueprint_id="family_accounting",
                         group_type="personal"))
    db.add(UserAccount(phone="972506666666", group_jid="dave_grp@g.us", role="owner"))
    db.commit()

    Session = sessionmaker(bind=db.get_bind())

    class _CM:
        def __init__(self, f):
            self._f = f
        def __enter__(self):
            self._s = self._f()
            return self._s
        def __exit__(self, *a):
            self._s.close()

    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with mpatch("app.admin.api.SessionLocal", side_effect=lambda: _CM(Session)):
        client = TestClient(app)
        resp = client.patch("/admin/api/people/972506666666",
                            json={"private_group_jid": "dave_grp@g.us"})
    assert resp.status_code == 200

    verify = Session()
    profile = verify.query(UserProfile).filter_by(phone="972506666666").first()
    assert profile is not None
    assert profile.private_group_jid == "dave_grp@g.us"
    verify.close()
