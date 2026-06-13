from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from app.db.models import (
    UserAccount, GroupRegistry, AdminNumbers, UserProfile, Blueprint,
)
from app.accounting.account_service import AccountService


def _seed_blueprint(db):
    if db.query(Blueprint).filter_by(id="family_accounting").first() is None:
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
    assert resolved is not None
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
    assert resolved is not None
    db.refresh(conf)
    assert conf.status == "rejected"


def test_handle_confirmation_reply_returns_false_when_no_pending(db):
    svc = AccountService()
    result = svc.handle_confirmation_reply(db, "grp@g.us", "972500", "yes")
    assert result is None


from decimal import Decimal
from datetime import date
from app.db.models import LedgerEntry


@pytest.mark.asyncio
async def test_process_first_party_writes_entry_and_notifies(db):
    """Sender acknowledges own debt (1st-party) → written immediately, creditor notified."""
    _seed_group(db, "eran_grp@g.us")
    _seed_user(db, "9725200", "eran_grp@g.us")  # Eran — creditor
    _seed_group(db, "eden_grp@g.us")
    _seed_user(db, "9725210", "eden_grp@g.us")  # Eden — reporter/debtor

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        result = await svc.process_transaction(
            db=db,
            reporter_phone="9725210",       # Eden
            reporter_group_jid="eden_grp@g.us",
            payer_phone="9725200",           # Eran paid → Eden owes Eran
            debtor_phone="9725210",          # Eden is the debtor
            amount_ils=Decimal("100"),
            description="dinner",
            transaction_date=date.today(),
        )

    # Ledger entry written immediately
    entry = db.query(LedgerEntry).first()
    assert entry is not None
    assert entry.from_phone == "9725210"
    assert entry.to_phone == "9725200"
    # Creditor (Eran) notified
    mock_bc.send_message.assert_awaited_once()
    assert "9725200" in result or "notified" in result.lower()


@pytest.mark.asyncio
async def test_process_second_party_creates_confirmation(db):
    """Sender claims credit (2nd-party) → confirmation requested from debtor."""
    _seed_group(db, "eden_grp2@g.us")
    _seed_user(db, "9725220", "eden_grp2@g.us")  # Eden — reporter/creditor
    _seed_group(db, "tal_grp4@g.us")
    _seed_user(db, "9725230", "tal_grp4@g.us")   # Tal — debtor

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        result = await svc.process_transaction(
            db=db,
            reporter_phone="9725220",        # Eden claims Tal owes her
            reporter_group_jid="eden_grp2@g.us",
            payer_phone="9725220",           # Eden is the creditor/payer
            debtor_phone="9725230",          # Tal is debtor
            amount_ils=Decimal("80"),
            description="taxi",
            transaction_date=date.today(),
        )

    # No ledger entry yet — waiting for confirmation
    assert db.query(LedgerEntry).count() == 0
    # Confirmation row created
    conf = db.query(CrossGroupConfirmation).first()
    assert conf is not None
    assert conf.target_phone == "9725230"
    assert conf.status == "pending"
    # Tal notified
    mock_bc.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_inbound — LID-safe group-JID-first strategy
# ---------------------------------------------------------------------------

from app.db.models import Household, HouseholdMember


def _seed_household(db, phone: str, group_jid: str, blueprint_id: str = "family_accounting") -> tuple:
    """Create Household + HouseholdMember + GroupRegistry so FK constraints hold."""
    _seed_blueprint(db)
    g = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
    if g is None:
        g = GroupRegistry(group_jid=group_jid, blueprint_id=blueprint_id, group_type="personal")
        db.add(g)
    h = Household(name="Test Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(household_id=h.id, phone=phone, private_group_jid=group_jid)
    db.add(m)
    db.commit()
    return h, m


def test_resolve_inbound_group_jid_first_returns_canonical_phone(db):
    """When HouseholdMember exists for the group, use its phone — ignores sender JID."""
    _seed_household(db, "972501234567", "alice_priv@g.us")
    svc = AccountService()
    # sender is a LID-format JID — completely different from the stored phone
    phone, household_id = svc.resolve_inbound(db, "alice_priv@g.us", "8650248708313:3@lid")
    assert phone == "972501234567"
    assert household_id is not None


def test_resolve_inbound_group_jid_first_ignores_lid_sender(db):
    """Canonical phone is returned regardless of whether sender is E.164 or LID."""
    _seed_household(db, "972509876543", "bob_priv@g.us")
    svc = AccountService()
    # Even if sender happens to be phone-format but WRONG phone, group-JID lookup wins
    phone, _ = svc.resolve_inbound(db, "bob_priv@g.us", "972500000001@s.whatsapp.net")
    assert phone == "972509876543"


def test_resolve_inbound_falls_back_to_sender_when_no_household_member(db):
    """No HouseholdMember for the group → fall back to extracting phone from sender JID."""
    _seed_blueprint(db)
    svc = AccountService()
    phone, household_id = svc.resolve_inbound(db, "unknown_grp@g.us", "972507654321@s.whatsapp.net")
    assert phone == "972507654321"
    assert household_id is None


def test_resolve_inbound_lid_sender_fallback_returns_lid_numeric(db):
    """LID sender + no HouseholdMember → returns LID numeric (acceptable degraded mode)."""
    _seed_blueprint(db)
    svc = AccountService()
    # LID numerics are long enough to pass normalize_phone's \d{7,18} check
    phone, household_id = svc.resolve_inbound(db, "unknown_grp@g.us", "8650248708313:3@lid")
    assert phone == "8650248708313"
    assert household_id is None


def test_resolve_inbound_household_id_returned(db):
    """household_id from HouseholdMember is propagated to caller."""
    h, m = _seed_household(db, "972501112222", "carol_priv@g.us")
    svc = AccountService()
    phone, household_id = svc.resolve_inbound(db, "carol_priv@g.us", "whatever:3@lid")
    assert household_id == h.id
