from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import ScheduledMessage
from tests.conftest import SessionCM


@pytest.mark.asyncio
async def test_due_message_is_sent_and_marked(db):
    now = datetime.now(timezone.utc)
    msg = ScheduledMessage(
        group_jid="123@g.us",
        to_phone="972500000001",
        message="pay Dana",
        send_at=now - timedelta(minutes=1),
        sent=False,
        created_at=now,
    )
    db.add(msg)
    db.commit()
    msg_id = msg.id

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_called_once()
    db.expire_all()
    updated = db.get(ScheduledMessage, msg_id)
    assert updated.sent is True


@pytest.mark.asyncio
async def test_future_message_is_not_sent(db):
    now = datetime.now(timezone.utc)
    msg = ScheduledMessage(
        group_jid="123@g.us",
        to_phone="972500000001",
        message="future reminder",
        send_at=now + timedelta(hours=1),
        sent=False,
        created_at=now,
    )
    db.add(msg)
    db.commit()
    msg_id = msg.id

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_not_called()
    db.expire_all()
    updated = db.get(ScheduledMessage, msg_id)
    assert updated.sent is False


@pytest.mark.asyncio
async def test_already_sent_message_is_not_resent(db):
    now = datetime.now(timezone.utc)
    msg = ScheduledMessage(
        group_jid="123@g.us",
        to_phone="972500000001",
        message="already sent",
        send_at=now - timedelta(minutes=5),
        sent=True,
        created_at=now,
    )
    db.add(msg)
    db.commit()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=SessionCM(db)), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_not_called()
