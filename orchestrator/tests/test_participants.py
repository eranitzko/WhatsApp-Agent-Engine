import pytest
from datetime import datetime, timezone
from app.db.models import GroupParticipant


def test_participant_insert_and_fetch(db):
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972501234567",
        push_name="Eran",
        status="active",
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert fetched.push_name == "Eran"
    assert fetched.admin_name is None
    assert fetched.is_household is False
    assert fetched.status == "active"
    assert fetched.removed_at is None
    assert fetched.joined_at is not None


def test_participant_admin_name_override(db):
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972501234567",
        push_name="Eran W",
        admin_name="Eran",
        is_household=True,
        status="active",
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert fetched.admin_name == "Eran"
    assert fetched.is_household is True


def test_participant_removed_keeps_row(db):
    from datetime import datetime, timezone
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972509999999",
        push_name="Tomer",
        status="removed",
        removed_at=datetime.now(timezone.utc),
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972509999999"))
    assert fetched.status == "removed"
    assert fetched.removed_at is not None
