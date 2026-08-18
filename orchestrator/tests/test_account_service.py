from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from app.db.models import (
    UserAccount, GroupRegistry, AdminNumbers, UserProfile,
)
from app.accounting.account_service import AccountService
from tests.conftest import seed_blueprint, seed_group, seed_household


# Thin wrappers around the shared conftest fixtures, kept local (not fully
# inlined at call sites) because this file's tests need a "family_accounting"
# blueprint with custom fields (model, tools_enabled) that the shared
# seed_blueprint's defaults don't provide — seed_group/seed_household's
# internal auto-seed of "family_accounting" then becomes a no-op here,
# since seed_blueprint is idempotent by id.
def _seed_blueprint(db):
    seed_blueprint(
        db, id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )


def _seed_group(db, jid: str, group_type: str = "personal") -> GroupRegistry:
    _seed_blueprint(db)
    return seed_group(db, jid, blueprint_id="family_accounting", group_type=group_type)


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


@pytest.mark.asyncio
async def test_request_confirmation_delivery_failure_does_not_roll_back_other_pending_work(db):
    """A failed bridge send must only discard THIS confirmation, not other
    not-yet-committed work already staged in the same session (e.g. a sibling
    split share, or the split header itself) — see process_split, which stages
    several objects in one session before any of them commits."""
    _seed_group(db, "tal_grp3@g.us")
    _seed_user(db, "972513", "tal_grp3@g.us")
    svc = AccountService()

    # Simulate other work already staged earlier in the same session/transaction,
    # not yet committed — mirrors process_split's self_confirmed row + header.
    sibling = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972500",
        target_group_jid="eden_grp@g.us",
        action_type="split_share",
        action_payload="{}",
        status="self_confirmed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(sibling)
    db.flush()
    sibling_id = sibling.id

    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock(side_effect=RuntimeError("bridge unreachable"))
        with pytest.raises(RuntimeError):
            await svc.request_confirmation(
                db=db,
                initiator_phone="972500",
                initiator_group_jid="eden_grp@g.us",
                target_phone="972513",
                action_type="record_expense",
                action_payload={"amount_ils": "100.00"},
                confirmation_message="Tal, Eden says you owe ₪100. Confirm?",
            )

    # The sibling row staged before the failed call must survive.
    assert db.query(CrossGroupConfirmation).filter_by(id=sibling_id).first() is not None
    # The failed confirmation itself must NOT have been persisted.
    assert db.query(CrossGroupConfirmation).filter_by(target_phone="972513").first() is None


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


def test_handle_confirmation_reply_recognizes_word_used_elsewhere(db):
    """Regression: handle_confirmation_reply had its own independent yes/no
    word list ({"yes","כן","y","אישור"} / {"no","לא","n","ביטול"}), missed by
    Task 4's original audit of the other 7 call sites. It must recognize the
    full union in app.agent.reply_words, e.g. "confirm" (accepted by
    ConfirmationStore/MultiConfirmationStore but not, until this fix, here)."""
    _seed_group(db, "tal_grp2b@g.us")
    _seed_user(db, "972512", "tal_grp2b@g.us")
    now = datetime.now(timezone.utc)
    conf = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972512",
        target_group_jid="tal_grp2b@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "50.00"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    resolved = svc.handle_confirmation_reply(db, "tal_grp2b@g.us", "972512", "confirm")
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

from app.db.models import HouseholdMember


def _seed_household(db, phone: str, group_jid: str, blueprint_id: str = "family_accounting") -> tuple:
    """Create Household + HouseholdMember + GroupRegistry so FK constraints hold.

    Kept as a local wrapper (not inlined) for the same reason as _seed_blueprint
    above: pre-seeds the custom "family_accounting" blueprint fields this file
    needs before delegating to the shared seed_household, whose own auto-seed
    becomes a no-op for that id.
    """
    _seed_blueprint(db)
    return seed_household(db, phone, group_jid, blueprint_id=blueprint_id)


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


def test_resolve_inbound_known_lid_resolves_in_shared_group(db):
    """Regression: a SHARED group (not anyone's private_group_jid) where the
    sender arrives as a LID must still resolve to the real phone once that
    LID is recorded via UserProfile.known_lid — this is what main.py's
    is_admin check now relies on for shared groups, where the existing
    private_group_jid-based strategies don't apply at all."""
    h, _ = _seed_household(db, "972528695501", "sivan_priv@g.us")
    db.add(UserProfile(phone="972528695501", known_lid="8650248708313"))
    db.commit()

    svc = AccountService()
    # "shared_grp@g.us" is a group nobody's private_group_jid points to.
    phone, household_id = svc.resolve_inbound(db, "shared_grp@g.us", "8650248708313@lid")
    assert phone == "972528695501"
    assert household_id == h.id


def test_resolve_inbound_unknown_lid_in_shared_group_falls_back(db):
    """An unrecognized LID in a shared group still falls back to the raw
    numeric (degraded but non-fatal), exactly as before this fix."""
    svc = AccountService()
    phone, household_id = svc.resolve_inbound(db, "shared_grp2@g.us", "6541369471061@lid")
    assert phone == "6541369471061"
    assert household_id is None


# ── Regression: bilateral netting of payment leftover (previously silently lost) ─

def _seed_debt(db, household_id, group_jid, from_phone, to_phone, amount, settled="0", desc="x"):
    entry = LedgerEntry(
        transaction_id=f"debt-{from_phone}-{to_phone}-{amount}-{desc}",
        entry_type="debt",
        household_id=household_id,
        group_jid=group_jid,
        from_phone=from_phone,
        to_phone=to_phone,
        amount_ils=Decimal(amount),
        amount_settled_ils=Decimal(settled),
        description=desc,
        transaction_date=date.today(),
    )
    db.add(entry)
    db.commit()
    return entry


@pytest.mark.asyncio
async def test_apply_payment_fifo_leftover_nets_against_reverse_direction_debt(db):
    """Regression for the bug found via production verification: a payment
    exceeding same-direction open debts used to silently drop the excess.
    It must now apply the excess FIFO against the reverse-direction debt."""
    h, _ = _seed_household(db, "9725301", "eran_grp@g.us")
    db.add(HouseholdMember(household_id=h.id, phone="9725302", private_group_jid="sivan_grp@g.us"))
    db.commit()

    debt_eran_owes_sivan = _seed_debt(db, h.id, "eran_grp@g.us", "9725301", "9725302", "100", desc="a")
    debt_sivan_owes_eran = _seed_debt(db, h.id, "eran_grp@g.us", "9725302", "9725301", "200", desc="b")

    svc = AccountService()
    await svc._apply_payment_fifo(
        db, "eran_grp@g.us", payer_phone="9725302", payee_phone="9725301",
        amount_ils=Decimal("250"), payment_date=date.today(), household_id=h.id,
    )

    db.refresh(debt_sivan_owes_eran)
    db.refresh(debt_eran_owes_sivan)
    assert debt_sivan_owes_eran.amount_settled_ils == Decimal("200")  # fully settled, same-direction
    assert debt_eran_owes_sivan.amount_settled_ils == Decimal("50")   # 50 leftover nets the reverse debt

    # No residual credit needed — the 50 leftover was fully absorbed by the reverse debt.
    all_entries = db.query(LedgerEntry).all()
    assert len(all_entries) == 3  # 2 debts + 1 payment leg, no extra credit entry


@pytest.mark.asyncio
async def test_apply_payment_fifo_residual_leftover_becomes_credit_entry(db):
    """When leftover remains even after bilateral netting (payment exceeds
    every known obligation either way), it must be recorded as an open
    reverse-direction debt rather than vanishing, so it automatically offsets
    the payee's next debt to the payer."""
    h, _ = _seed_household(db, "9725401", "eran_grp2@g.us")
    db.add(HouseholdMember(household_id=h.id, phone="9725402", private_group_jid="sivan_grp2@g.us"))
    db.commit()

    _seed_debt(db, h.id, "eran_grp2@g.us", "9725401", "9725402", "30", desc="small")   # Eran owes Sivan 30
    _seed_debt(db, h.id, "eran_grp2@g.us", "9725402", "9725401", "200", desc="big")    # Sivan owes Eran 200

    svc = AccountService()
    await svc._apply_payment_fifo(
        db, "eran_grp2@g.us", payer_phone="9725402", payee_phone="9725401",
        amount_ils=Decimal("250"), payment_date=date.today(), household_id=h.id,
    )

    # 250 - 200 (same-direction) = 50 leftover; 50 - 30 (reverse debt) = 20 residual
    credit = (
        db.query(LedgerEntry)
        .filter_by(from_phone="9725401", to_phone="9725402", entry_type="debt")
        .filter(LedgerEntry.description.like("Credit from overpayment%"))
        .first()
    )
    assert credit is not None
    assert credit.amount_ils == Decimal("20")
    assert credit.amount_settled_ils == Decimal("0")


@pytest.mark.asyncio
async def test_apply_payment_fifo_no_leftover_unaffected(db):
    """Payment that doesn't exceed same-direction open debts behaves exactly
    as before — no reverse-direction or credit side effects."""
    h, _ = _seed_household(db, "9725501", "eran_grp3@g.us")
    db.add(HouseholdMember(household_id=h.id, phone="9725502", private_group_jid="sivan_grp3@g.us"))
    db.commit()

    debt_eran_owes_sivan = _seed_debt(db, h.id, "eran_grp3@g.us", "9725501", "9725502", "100", desc="c")
    debt_sivan_owes_eran = _seed_debt(db, h.id, "eran_grp3@g.us", "9725502", "9725501", "200", desc="d")

    svc = AccountService()
    await svc._apply_payment_fifo(
        db, "eran_grp3@g.us", payer_phone="9725502", payee_phone="9725501",
        amount_ils=Decimal("120"), payment_date=date.today(), household_id=h.id,
    )

    db.refresh(debt_sivan_owes_eran)
    db.refresh(debt_eran_owes_sivan)
    assert debt_sivan_owes_eran.amount_settled_ils == Decimal("120")
    assert debt_eran_owes_sivan.amount_settled_ils == Decimal("0")  # untouched


def test_balance_update_message_formats_debt_owed_and_settled(db):
    """New confirmation copy: 'Updated. Your balance with X is now: ...'"""
    h, _ = _seed_household(db, "9725601", "eran_grp4@g.us")
    db.add(HouseholdMember(household_id=h.id, phone="9725602", private_group_jid="sivan_grp4@g.us"))
    db.add(UserProfile(phone="9725602", display_name="Sivan"))
    db.commit()

    _seed_debt(db, h.id, "eran_grp4@g.us", "9725601", "9725602", "40", desc="e")

    svc = AccountService()
    msg = svc.balance_update_message(db, "eran_grp4@g.us", "9725601", "9725602", h.id)
    assert msg == "Updated. Your balance with Sivan is now: you owe Sivan ₪40.00."

    # Fully settle it — balance should now read "settled up"
    entry = db.query(LedgerEntry).filter_by(from_phone="9725601", to_phone="9725602").first()
    entry.amount_settled_ils = Decimal("40")
    db.commit()
    msg2 = svc.balance_update_message(db, "eran_grp4@g.us", "9725601", "9725602", h.id)
    assert msg2 == "Updated. Your balance with Sivan is now: settled up."
