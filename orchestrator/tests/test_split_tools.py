import json
from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    UserAccount, GroupRegistry, Blueprint,
    SplitTransaction, CrossGroupConfirmation, LedgerEntry,
)
from app.accounting.account_service import AccountService
from tests.conftest import SessionCM


def _setup(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    for jid, phone in [
        ("eran_g@g.us", "9725300"),
        ("eden_g@g.us", "9725310"),
        ("tal_g@g.us", "9725320"),
    ]:
        db.add(GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type="personal"))
        db.add(UserAccount(phone=phone, group_jid=jid, role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_process_split_creates_split_transaction(db):
    _setup(db)
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        split = await svc.process_split(
            db=db,
            reporter_phone="9725300",
            reporter_group_jid="eran_g@g.us",
            payer_phone="9725300",
            shares=[
                {"phone": "9725310", "amount_ils": Decimal("66.67")},
                {"phone": "9725320", "amount_ils": Decimal("66.67")},
            ],
            total_amount=Decimal("200"),
            description="restaurant",
            transaction_date=date.today(),
        )
    assert split.id is not None
    assert split.status == "pending"
    # Payer is reporter → both shares are 2nd-party → 2 confirmation rows
    confs = db.query(CrossGroupConfirmation).filter_by(split_transaction_id=split.id).all()
    assert len(confs) == 2
    phones = {c.target_phone for c in confs}
    assert phones == {"9725310", "9725320"}


@pytest.mark.asyncio
async def test_process_split_reporter_is_participant_writes_own_share_pending(db):
    _setup(db)
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        # Eden reports that Eran paid; Eden's own share is 1st-party
        split = await svc.process_split(
            db=db,
            reporter_phone="9725310",          # Eden is reporter
            reporter_group_jid="eden_g@g.us",
            payer_phone="9725300",             # Eran paid
            shares=[
                {"phone": "9725310", "amount_ils": Decimal("66.67")},  # Eden — 1st-party
                {"phone": "9725320", "amount_ils": Decimal("66.67")},  # Tal — 2nd-party
            ],
            total_amount=Decimal("200"),
            description="restaurant",
            transaction_date=date.today(),
        )
    assert split.status == "pending"
    confs = db.query(CrossGroupConfirmation).filter_by(split_transaction_id=split.id).all()
    # Only Tal's share needs a confirmation row; Eden's is 1st-party stored as self_confirmed
    assert len(confs) == 2  # one self_confirmed (Eden) + one pending (Tal)
    pending_confs = [c for c in confs if c.status == "pending"]
    assert len(pending_confs) == 1
    assert pending_confs[0].target_phone == "9725320"
    assert split.reporter_phone == "9725310"


@pytest.mark.asyncio
async def test_process_split_first_share_notify_failure_keeps_share_pending_for_resend(db):
    """If the FIRST co-debtor's confirmation can't be delivered (e.g. a transient
    bridge outage), the split header, that share, and any later shares in the
    same call must still be recorded — not silently destroyed because one
    recipient was unreachable at that moment. The undelivered share stays
    pending so resend_confirmation can retry it, instead of forcing the
    reporter to redo the whole split from scratch."""
    _setup(db)
    svc = AccountService()

    async def fake_send(jid, _msg):
        if jid == "tal_g@g.us":
            raise RuntimeError("bridge unreachable")

    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock(side_effect=fake_send)
        split = await svc.process_split(
            db=db,
            reporter_phone="9725300",
            reporter_group_jid="eran_g@g.us",
            payer_phone="9725300",
            shares=[
                {"phone": "9725320", "amount_ils": Decimal("66.67")},  # Tal — fails first
                {"phone": "9725310", "amount_ils": Decimal("66.67")},  # Eden — succeeds after
            ],
            total_amount=Decimal("200"),
            description="restaurant",
            transaction_date=date.today(),
        )

    assert db.query(SplitTransaction).filter_by(id=split.id).first() is not None
    confs = db.query(CrossGroupConfirmation).filter_by(split_transaction_id=split.id).all()
    by_phone = {c.target_phone: c for c in confs}
    assert set(by_phone) == {"9725310", "9725320"}  # both shares survive
    assert by_phone["9725320"].status == "pending"  # Tal's — undelivered, kept for resend
    assert by_phone["9725310"].status == "pending"  # Eden's — delivered normally


@pytest.mark.asyncio
async def test_decline_suspends_split(db):
    _setup(db)
    now = datetime.now(timezone.utc)
    split = SplitTransaction(
        reporter_group_jid="eran_g@g.us",
        reporter_phone="972530",
        payer_phone="972530",
        total_amount=Decimal("200"),
        description="restaurant",
        status="pending",
    )
    db.add(split)
    db.flush()
    conf = CrossGroupConfirmation(
        split_transaction_id=split.id,
        initiator_phone="972530",
        initiator_group_jid="eran_g@g.us",
        target_phone="972532",
        target_group_jid="tal_g@g.us",
        action_type="split_share",
        action_payload='{"amount_ils": "66.67"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.handle_split_decline(db, conf)

    db.refresh(split)
    assert split.status == "suspended"
    # All parties notified
    assert mock_bc.send_message.await_count >= 1


def _make_pending_split(db, num_shares: int = 3):
    """Two-of-three-style split fixture: one SplitTransaction, N pending
    CrossGroupConfirmation shares, each targeting its own group."""
    now = datetime.now(timezone.utc)
    split = SplitTransaction(
        reporter_group_jid="eran_g@g.us",
        reporter_phone="972530",
        payer_phone="972530",
        total_amount=Decimal("90"),
        description="dinner",
        status="pending",
    )
    db.add(split)
    db.flush()

    confs = []
    for i in range(num_shares):
        phone = f"97253{i+1}"
        jid = f"share{i+1}_g@g.us"
        db.add(GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type="personal"))
        db.add(UserAccount(phone=phone, group_jid=jid, role="owner"))
        payload = json.dumps({
            "group_jid": "eran_g@g.us",
            "household_id": None,
            "payer_phone": "972530",
            "debtor_phone": phone,
            "amount_ils": "30.00",
            "description": "dinner",
            "transaction_date": date.today().isoformat(),
            "split_transaction_id": split.id,
        })
        conf = CrossGroupConfirmation(
            split_transaction_id=split.id,
            initiator_phone="972530",
            initiator_group_jid="eran_g@g.us",
            target_phone=phone,
            target_group_jid=jid,
            action_type="split_share",
            action_payload=payload,
            status="pending",
            expires_at=now + timedelta(hours=24),
        )
        db.add(conf)
        confs.append(conf)
    db.commit()
    return split, confs


@pytest.mark.asyncio
async def test_finalize_split_early_confirmer_gets_immediate_ack_no_ledger_yet(db):
    """Confirming one of several shares must give that person immediate
    feedback — not silence until everyone else also responds — but must NOT
    write the ledger yet, since a later decline should still be able to
    cleanly cancel the whole split before anything is recorded."""
    _setup(db)
    split, confs = _make_pending_split(db, num_shares=3)
    confs[0].status = "confirmed"
    db.commit()

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.finalize_split(db, split, just_confirmed=confs[0])

    mock_bc.send_message.assert_awaited_once()
    call_args = mock_bc.send_message.await_args
    assert call_args.args[0] == confs[0].target_group_jid
    assert db.query(LedgerEntry).count() == 0
    db.refresh(split)
    assert split.status == "pending"


@pytest.mark.asyncio
async def test_finalize_split_early_confirmer_status_change_is_committed(db):
    """Regression: in production, just_confirmed.status is only flushed (not
    yet committed) by handle_confirmation_reply before finalize_split runs —
    see test_handle_confirmation_reply_status_flip_is_rollback_safe. The
    early-confirmer branch (not all shares done yet) must therefore commit
    itself, or the confirmer's own "yes" would silently roll back the moment
    the request session closes without any further write (found via a
    20-round admin-churn stress test)."""
    _setup(db)
    split, confs = _make_pending_split(db, num_shares=3)
    confs[0].status = "confirmed"
    db.flush()  # NOT db.commit() — matches real handle_confirmation_reply behavior

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.finalize_split(db, split, just_confirmed=confs[0])

    db.rollback()  # if finalize_split didn't commit, this would undo the flush too

    reloaded = db.query(CrossGroupConfirmation).filter_by(id=confs[0].id).first()
    assert reloaded.status == "confirmed"


@pytest.mark.asyncio
async def test_finalize_split_commits_and_acks_everyone_once_last_share_confirms(db):
    """Once every share is confirmed, all ledger entries commit together and
    every participant (not just the last confirmer) gets their own ack."""
    _setup(db)
    split, confs = _make_pending_split(db, num_shares=2)
    for c in confs:
        c.status = "confirmed"
    db.commit()

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.finalize_split(db, split, just_confirmed=confs[-1])

    assert db.query(LedgerEntry).count() == 2
    db.refresh(split)
    assert split.status == "confirmed"
    sent_to = {call.args[0] for call in mock_bc.send_message.await_args_list}
    assert confs[0].target_group_jid in sent_to
    assert confs[1].target_group_jid in sent_to
    assert split.reporter_group_jid in sent_to  # "All shares confirmed"


# ---------------------------------------------------------------------------
# Tool registration & executor tests
# ---------------------------------------------------------------------------

def test_split_tools_registration():
    from app.tools.split_tools import get_split_tools
    tools = get_split_tools()
    assert "record_split" in tools
    entry = tools["record_split"]
    assert "schema" in entry and "executor" in entry
    assert entry["schema"]["name"] == "record_split"


@pytest.mark.asyncio
async def test_record_split_equal_split(db):
    _setup(db)
    from app.tools.split_tools import get_split_tools, set_account_service as set_svc
    from unittest.mock import MagicMock
    mock_svc = MagicMock()
    mock_split = SplitTransaction(
        reporter_group_jid="eran_g@g.us", reporter_phone="972530",
        payer_phone="972530", total_amount=Decimal("200"), description="restaurant",
        status="pending",
    )
    mock_split.id = "split-1"
    mock_svc.process_split = AsyncMock(return_value=mock_split)
    set_svc(mock_svc)

    with patch("app.tools.split_tools.SessionLocal", return_value=SessionCM(db)), \
         patch("app.tools.split_tools.to_ils", new=AsyncMock(return_value=Decimal("200"))):
        tools = get_split_tools()
        result = await tools["record_split"]["executor"](
            {
                "payer_phone": "972530",
                "all_phones": ["972530", "972531", "972532"],
                "amount": 200,
                "currency": "ILS",
                "description": "restaurant",
            },
            group_jid="eran_g@g.us",
            sender="972530@s.whatsapp.net",
            is_admin=False,
        )

    mock_svc.process_split.assert_awaited_once()
    call_kwargs = mock_svc.process_split.call_args[1]
    shares = call_kwargs["shares"]
    # 3 participants, payer excluded, 2 non-payer shares
    assert len(shares) == 2
    # Equal split: 200 / 3 ≈ 66.67
    assert all(abs(float(s["amount_ils"]) - 66.67) < 0.1 for s in shares)
    set_svc(None)


@pytest.mark.asyncio
async def test_execute_record_split_uses_resolved_phone_over_raw_sender(db):
    """Regression: sender_phone must come from ctx["resolved_phone"], not a raw
    LID split — using the raw LID misattributes reporter_phone in shared groups."""
    _setup(db)
    from app.tools.split_tools import get_split_tools, set_account_service as set_svc
    from unittest.mock import MagicMock
    mock_svc = MagicMock()
    mock_split = SplitTransaction(
        reporter_group_jid="eran_g@g.us", reporter_phone="972523206175",
        payer_phone="972530", total_amount=Decimal("100"), description="test",
        status="pending",
    )
    mock_split.id = "split-2"
    mock_svc.process_split = AsyncMock(return_value=mock_split)
    set_svc(mock_svc)

    with patch("app.tools.split_tools.SessionLocal", return_value=SessionCM(db)), \
         patch("app.tools.split_tools.to_ils", new=AsyncMock(return_value=Decimal("100"))):
        tools = get_split_tools()
        await tools["record_split"]["executor"](
            {
                "payer_phone": "972530",
                "all_phones": ["972530", "972531"],
                "amount": 100,
                "currency": "ILS",
                "description": "test",
            },
            group_jid="eran_g@g.us",
            sender="175715853041683@lid",
            resolved_phone="972523206175",
            is_admin=False,
        )

    mock_svc.process_split.assert_awaited_once()
    call_kwargs = mock_svc.process_split.call_args[1]
    assert call_kwargs["reporter_phone"] == "972523206175"
    set_svc(None)
