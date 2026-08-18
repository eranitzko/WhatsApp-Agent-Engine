from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    UserAccount, GroupRegistry, Blueprint,
    SplitTransaction, CrossGroupConfirmation,
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
async def test_process_split_first_share_notify_failure_does_not_lose_split_or_later_shares(db):
    """If the FIRST co-debtor's confirmation can't be delivered (e.g. a transient
    bridge outage), the split header and any later, successfully-delivered
    shares in the same call must still be recorded — not silently destroyed
    because one recipient was unreachable at that moment."""
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
    phones = {c.target_phone for c in confs}
    assert phones == {"9725310"}  # only Eden's — Tal's failed delivery was discarded


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
