from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest

from app.db.models import (
    CrossGroupConfirmation, SplitTransaction, GroupRegistry,
    UserAccount, Blueprint, AdminNumbers,
)
from app.scheduler import _expire_cross_group_confirmations


def _seed(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    for jid, phone in [("grp_a@g.us", "972540"), ("grp_b@g.us", "972541")]:
        db.add(GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type="personal"))
        db.add(UserAccount(phone=phone, group_jid=jid, role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_expire_cross_group_confirmations_flips_timed_out(db):
    _seed(db)
    now = datetime.now(timezone.utc)
    expired_conf = CrossGroupConfirmation(
        initiator_phone="972540",
        initiator_group_jid="grp_a@g.us",
        target_phone="972541",
        target_group_jid="grp_b@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "100"}',
        status="pending",
        expires_at=now - timedelta(hours=1),  # already expired
    )
    db.add(expired_conf)
    db.commit()

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.scheduler.bridge_client") as mock_bc:
        mock_sl.return_value.__enter__ = lambda s: db
        mock_sl.return_value.__exit__ = lambda s, *a: None
        mock_bc.send_message = AsyncMock()
        await _expire_cross_group_confirmations()

    db.refresh(expired_conf)
    assert expired_conf.status == "timed_out"
    # Both parties notified
    assert mock_bc.send_message.await_count == 2


@pytest.mark.asyncio
async def test_active_confirmation_not_expired(db):
    _seed(db)
    now = datetime.now(timezone.utc)
    active_conf = CrossGroupConfirmation(
        initiator_phone="972540",
        initiator_group_jid="grp_a@g.us",
        target_phone="972541",
        target_group_jid="grp_b@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "100"}',
        status="pending",
        expires_at=now + timedelta(hours=23),  # not yet expired
    )
    db.add(active_conf)
    db.commit()

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.scheduler.bridge_client") as mock_bc:
        mock_sl.return_value.__enter__ = lambda s: db
        mock_sl.return_value.__exit__ = lambda s, *a: None
        mock_bc.send_message = AsyncMock()
        await _expire_cross_group_confirmations()

    db.refresh(active_conf)
    assert active_conf.status == "pending"
    mock_bc.send_message.assert_not_awaited()
