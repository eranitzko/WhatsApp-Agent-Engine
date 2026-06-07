from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    UserAccount, GroupRegistry, Blueprint,
    SplitTransaction, CrossGroupConfirmation,
)
from app.accounting.account_service import AccountService


def _setup(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    for jid, phone in [
        ("eran_g@g.us", "972530"),
        ("eden_g@g.us", "972531"),
        ("tal_g@g.us", "972532"),
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
            reporter_phone="972530",
            reporter_group_jid="eran_g@g.us",
            payer_phone="972530",
            shares=[
                {"phone": "972531", "amount_ils": Decimal("66.67")},
                {"phone": "972532", "amount_ils": Decimal("66.67")},
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
    assert phones == {"972531", "972532"}


@pytest.mark.asyncio
async def test_process_split_reporter_is_participant_writes_own_share_pending(db):
    _setup(db)
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        # Eden reports that Eran paid; Eden's own share is 1st-party
        split = await svc.process_split(
            db=db,
            reporter_phone="972531",          # Eden is reporter
            reporter_group_jid="eden_g@g.us",
            payer_phone="972530",             # Eran paid
            shares=[
                {"phone": "972531", "amount_ils": Decimal("66.67")},  # Eden — 1st-party
                {"phone": "972532", "amount_ils": Decimal("66.67")},  # Tal — 2nd-party
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
    assert pending_confs[0].target_phone == "972532"
    assert split.reporter_phone == "972531"


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
# Helpers
# ---------------------------------------------------------------------------

class _CM:
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


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

    with patch("app.tools.split_tools.SessionLocal", return_value=_CM(db)), \
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
