"""Tests for deterministic outbound routing via primary_accounting_group_jid.

Coverage:
  1. Eden has TWO family_accounting groups + one invoice_curator group.
     A reports "Eden owes me ₪200." → confirmation lands in Eden's PRIMARY
     accounting group, never the second accounting group or the invoice_curator.
  2. Primary unset → falls back to first-registered accounting group (ordered
     by created_at), deterministically.
  3. Eden's "yes" reply from the primary group (LID-format sender) resolves
     inbound to canonical phone and matches the pending confirmation.
  4. Counterpart has NO family_accounting group → process_transaction returns
     a human-readable error, never silently drops.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    Blueprint, GroupRegistry, UserAccount, HouseholdMember, Household,
    CrossGroupConfirmation,
)
from app.accounting.account_service import AccountService


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _bp(db, blueprint_id: str) -> None:
    if db.query(Blueprint).filter_by(id=blueprint_id).first() is None:
        db.add(Blueprint(
            id=blueprint_id,
            display_name=blueprint_id,
            system_prompt="x",
            model="claude-sonnet-4-6",
            tools_enabled="[]",
        ))


def _group(db, jid: str, blueprint_id: str = "family_accounting",
           group_type: str = "personal") -> GroupRegistry:
    _bp(db, blueprint_id)
    g = GroupRegistry(group_jid=jid, blueprint_id=blueprint_id, group_type=group_type)
    db.add(g)
    return g


def _account(db, phone: str, group_jid: str,
             created_at: datetime | None = None) -> UserAccount:
    a = UserAccount(phone=phone, group_jid=group_jid, role="owner")
    if created_at:
        a.created_at = created_at
    db.add(a)
    return a


def _household_member(db, phone: str,
                      primary_accounting_group_jid: str | None = None,
                      private_group_jid: str | None = None) -> HouseholdMember:
    h = Household(name="Test Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(
        household_id=h.id,
        phone=phone,
        private_group_jid=private_group_jid,
        primary_accounting_group_jid=primary_accounting_group_jid,
    )
    db.add(m)
    return m


# ---------------------------------------------------------------------------
# Test 1: Primary accounting group wins over second accounting group and
#         invoice_curator group (blueprint filter enforced).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirmation_lands_in_primary_not_second_or_other_blueprint(db):
    """process_transaction sends confirmation to Eden's PRIMARY accounting group."""
    _group(db, "eden_acct1@g.us", "family_accounting")   # first registered
    _group(db, "eden_acct2@g.us", "family_accounting")   # second accounting group
    _group(db, "eden_inv@g.us",   "invoice_curator")     # different blueprint

    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2025, 1, 2, tzinfo=timezone.utc)
    _account(db, "972509000001", "eden_acct1@g.us", created_at=t0)
    _account(db, "972509000001", "eden_acct2@g.us", created_at=t1)
    _account(db, "972509000001", "eden_inv@g.us")

    _group(db, "alon_grp@g.us", "family_accounting")
    _account(db, "972509000002", "alon_grp@g.us")

    # Eden's primary is explicitly set to acct2 (the override, not the first)
    _household_member(db, "972509000001", primary_accounting_group_jid="eden_acct2@g.us")
    db.commit()

    svc = AccountService()
    sent_to: list[str] = []

    async def _fake_send(jid, msg):
        sent_to.append(jid)

    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = _fake_send
        result = await svc.process_transaction(
            db=db,
            reporter_phone="972509000002",    # Alon reports
            reporter_group_jid="alon_grp@g.us",
            payer_phone="972509000002",        # Alon paid
            debtor_phone="972509000001",       # Eden owes
            amount_ils=Decimal("200"),
            description="dinner",
            transaction_date=date.today(),
        )

    assert "972509000001" in result or "Confirmation" in result
    # Confirmation must arrive at acct2 (primary), never acct1 or invoice_curator
    assert "eden_acct2@g.us" in sent_to
    assert "eden_acct1@g.us" not in sent_to
    assert "eden_inv@g.us" not in sent_to

    conf = db.query(CrossGroupConfirmation).filter_by(target_phone="972509000001").first()
    assert conf is not None
    assert conf.target_group_jid == "eden_acct2@g.us"


# ---------------------------------------------------------------------------
# Test 2: Primary unset → first-registered accounting group (by created_at).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_unset_falls_back_to_first_registered(db):
    """With no explicit primary, routing uses the earliest created_at account."""
    _group(db, "eden_b1@g.us", "family_accounting")
    _group(db, "eden_b2@g.us", "family_accounting")

    t_first  = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t_second = datetime(2025, 6, 1, tzinfo=timezone.utc)
    # Register second group FIRST in the list to prove ordering by created_at, not insertion
    _account(db, "972509000011", "eden_b2@g.us", created_at=t_second)
    _account(db, "972509000011", "eden_b1@g.us", created_at=t_first)

    _group(db, "bob_grp@g.us", "family_accounting")
    _account(db, "972509000012", "bob_grp@g.us")
    db.commit()

    svc = AccountService()
    primary = svc.get_primary_accounting_group(db, "972509000011")
    assert primary == "eden_b1@g.us", f"Expected first-registered group, got {primary!r}"


def test_primary_set_overrides_first_registered(db):
    """Explicit primary_accounting_group_jid beats first-registered ordering."""
    _group(db, "x_early@g.us", "family_accounting")
    _group(db, "x_later@g.us", "family_accounting")

    t_early = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t_later = datetime(2025, 6, 1, tzinfo=timezone.utc)
    _account(db, "972509000021", "x_early@g.us", created_at=t_early)
    _account(db, "972509000021", "x_later@g.us", created_at=t_later)

    # Override: prefer the later group
    _household_member(db, "972509000021", primary_accounting_group_jid="x_later@g.us")
    db.commit()

    svc = AccountService()
    assert svc.get_primary_accounting_group(db, "972509000021") == "x_later@g.us"


# ---------------------------------------------------------------------------
# Test 3: "yes" reply from primary group with LID sender → resolves correctly.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lid_reply_from_primary_group_resolves_confirmation(db):
    """LID-format sender in Eden's primary group still matches her pending conf."""
    _group(db, "eden_primary@g.us", "family_accounting")
    _group(db, "eden_other@g.us",   "family_accounting")
    _account(db, "972509000031", "eden_primary@g.us")
    _account(db, "972509000031", "eden_other@g.us")

    _group(db, "alon_g@g.us", "family_accounting")
    _account(db, "972509000032", "alon_g@g.us")

    # Eden's private group (for resolve_inbound) is her primary accounting group
    _household_member(
        db, "972509000031",
        primary_accounting_group_jid="eden_primary@g.us",
        private_group_jid="eden_primary@g.us",
    )
    db.commit()

    # Seed a pending confirmation targeting Eden's primary group
    from app.db.models import Household
    h = db.query(Household).first()
    conf = CrossGroupConfirmation(
        initiator_phone="972509000032",
        initiator_group_jid="alon_g@g.us",
        target_phone="972509000031",
        target_group_jid="eden_primary@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils":"200.00"}',
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        household_id=h.id if h else None,
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    # Bridge delivers: jid=eden_primary@g.us, sender=LID-format
    phone, household_id = svc.resolve_inbound(db, "eden_primary@g.us", "8650248708313:3@lid")
    assert phone == "972509000031"

    # handle_confirmation_reply uses the resolved phone
    resolved = svc.handle_confirmation_reply(
        db, "eden_primary@g.us", phone, "yes", household_id=household_id
    )
    assert resolved is not None
    assert resolved.status == "confirmed"
    assert resolved.target_phone == "972509000031"


# ---------------------------------------------------------------------------
# Test 4: No family_accounting group → loud error, never silent drop.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_accounting_group_returns_error_string(db):
    """process_transaction returns a user-visible error when counterpart has no accounting group."""
    _group(db, "alon_g2@g.us", "family_accounting")
    _account(db, "972509000041", "alon_g2@g.us")

    # Eden exists as a contact but has only an invoice_curator group
    _group(db, "eden_invoice@g.us", "invoice_curator")
    _account(db, "972509000042", "eden_invoice@g.us")
    db.commit()

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        result = await svc.process_transaction(
            db=db,
            reporter_phone="972509000041",
            reporter_group_jid="alon_g2@g.us",
            payer_phone="972509000041",
            debtor_phone="972509000042",
            amount_ils=Decimal("100"),
            description="taxi",
            transaction_date=date.today(),
        )

    # Must contain a human-readable explanation — never empty / never just "ok"
    assert result
    assert "accounting" in result.lower() or "972509000042" in result
    # Bridge must not have been called (no message sent anywhere)
    mock_bc.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5: Blueprint filter — invoice_curator group is never returned.
# ---------------------------------------------------------------------------

def test_get_primary_accounting_group_filters_blueprint(db):
    """invoice_curator groups are invisible to get_primary_accounting_group."""
    _group(db, "inv_g@g.us", "invoice_curator")
    _account(db, "972509000051", "inv_g@g.us")
    db.commit()

    svc = AccountService()
    result = svc.get_primary_accounting_group(db, "972509000051")
    assert result is None, "invoice_curator group must never be returned as primary accounting"
