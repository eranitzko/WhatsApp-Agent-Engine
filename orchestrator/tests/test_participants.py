import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy.exc import IntegrityError
from app.db.models import GroupParticipant, AdminNumbers
from app.participants import build_participant_block
from tests.conftest import seed_blueprint, seed_group as _seed_group_shared


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


def test_participant_duplicate_pk_raises(db):
    row1 = GroupParticipant(group_jid="123@g.us", phone="972501111111")
    db.add(row1)
    db.commit()

    row2 = GroupParticipant(group_jid="123@g.us", phone="972501111111")
    db.add(row2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ── Task 3: upsert helper logic ────────────────────────────────────────────────

def _upsert(db, group_jid, phone, push_name=None, admin_name=None,
            is_household=False, status="active", removed_at=None):
    row = db.get(GroupParticipant, (group_jid, phone))
    if row is None:
        row = GroupParticipant(
            group_jid=group_jid, phone=phone, push_name=push_name,
            admin_name=admin_name, is_household=is_household,
            status=status, removed_at=removed_at,
        )
        db.add(row)
    else:
        if status != row.status:
            row.status = status
        if removed_at is not None:
            row.removed_at = removed_at
        if push_name is not None and row.admin_name is None and row.push_name != push_name:
            row.push_name = push_name
    db.commit()
    return row


def _seed_group(db):
    seed_blueprint(db, id="invoice_curator", display_name="IC")
    _seed_group_shared(db, "123@g.us", blueprint_id="invoice_curator")


def test_upsert_new_participant(db):
    _seed_group(db)
    _upsert(db, "123@g.us", "972501234567", push_name="Eran")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran"
    assert row.status == "active"


def test_upsert_updates_push_name_when_no_admin_name(db):
    _seed_group(db)
    _upsert(db, "123@g.us", "972501234567", push_name="Eran")
    _upsert(db, "123@g.us", "972501234567", push_name="Eran W")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran W"


def test_upsert_does_not_overwrite_admin_name(db):
    _seed_group(db)
    _upsert(db, "123@g.us", "972501234567", push_name="Eran W", admin_name="Eran")
    _upsert(db, "123@g.us", "972501234567", push_name="New Push Name")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran W"  # not updated because admin_name is set


def test_participant_remove_sets_status(db):
    _seed_group(db)
    _upsert(db, "123@g.us", "972501234567", push_name="Eran")
    _upsert(db, "123@g.us", "972501234567",
            status="removed", removed_at=datetime.now(timezone.utc))
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.status == "removed"
    assert row.removed_at is not None
    assert row.push_name == "Eran"


# ── Task 4: /sync bootstraps participants ─────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_bootstraps_participants(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    seed_blueprint(db, id="invoice_curator", display_name="IC")
    _seed_group_shared(db, "123@g.us", blueprint_id="invoice_curator")
    db.commit()

    from app.command_handler import CommandHandler
    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(return_value={
        "description": "Custom instructions here.",
        "participants": [
            {"jid": "972501234567@s.whatsapp.net", "isAdmin": False},
            {"jid": "972509876543@s.whatsapp.net", "isAdmin": True},
        ],
    })):
        reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")

    assert "synced" in reply.lower()
    db.expire_all()
    p1 = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    p2 = db.get(GroupParticipant, ("123@g.us", "972509876543"))
    assert p1 is not None and p1.status == "active"
    assert p2 is not None and p2.status == "active"


# ── Task 5: build_participant_block ───────────────────────────────────────────

def test_build_participant_block_basic(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972502222222",
                             push_name="Sivan", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", status="active"))
    db.commit()

    block = build_participant_block(db, "123@g.us")
    assert block is not None
    assert "972501111111" in block
    assert "972502222222" in block
    assert "Eden" in block
    assert "household" in block.lower() or "parents" in block.lower()


def test_build_participant_block_removed_included(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "456@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="456@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.add(GroupParticipant(group_jid="456@g.us", phone="972509999999",
                             push_name="Tomer", status="removed"))
    db.commit()

    block = build_participant_block(db, "456@g.us")
    assert "Tomer" in block
    assert "(removed)" in block


def test_build_participant_block_admin_name_takes_priority(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "789@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="789@g.us", phone="972501111111",
                             push_name="Eran W.", admin_name="Eran", status="active"))
    db.commit()

    block = build_participant_block(db, "789@g.us")
    assert "Eran" in block
    assert "Eran W." not in block


def test_build_participant_block_empty_group(db):
    block = build_participant_block(db, "no-such-group@g.us")
    assert block is None


def test_build_participant_block_marks_acl_admin_by_canonical_phone(db):
    """A participant whose GroupParticipant.phone is their real, canonical
    phone (already matching AdminNumbers directly) is marked '(admin)'."""
    seed_blueprint(db, id="fa2", display_name="FA")
    _seed_group_shared(db, "admin1@g.us", blueprint_id="fa2")
    db.add(AdminNumbers(phone_number="972501111111", label="Eran"))
    db.add(GroupParticipant(group_jid="admin1@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.add(GroupParticipant(group_jid="admin1@g.us", phone="972509999999",
                             push_name="Roni", status="active"))
    db.commit()

    block = build_participant_block(db, "admin1@g.us")
    assert "Eran (admin): 972501111111" in block
    assert "Roni: 972509999999" in block  # not an admin — no suffix


def test_build_participant_block_marks_acl_admin_via_known_lid(db):
    """Regression: a shared group where WhatsApp sends a LID (not the real
    phone) in GroupParticipant.phone — the participant must still be marked
    '(admin)' once their LID is recorded on UserProfile.known_lid, even
    though GroupParticipant.phone itself never matches AdminNumbers directly.
    This is exactly the bug that made the invoice_curator bot tell an actual
    admin (Sivan) that she wasn't one."""
    from app.db.models import UserProfile

    seed_blueprint(db, id="fa3", display_name="FA")
    _seed_group_shared(db, "admin2@g.us", blueprint_id="fa3")
    db.add(AdminNumbers(phone_number="972528695501", label="Sivan"))
    db.add(UserProfile(phone="972528695501", known_lid="8650248708313"))
    db.add(GroupParticipant(group_jid="admin2@g.us", phone="8650248708313",
                             push_name="Sivan Itzkovitch", status="active"))
    db.add(GroupParticipant(group_jid="admin2@g.us", phone="6541369471061",
                             push_name="Roni", status="active"))
    db.commit()

    block = build_participant_block(db, "admin2@g.us")
    # Displayed phone is the resolved CANONICAL phone, not the raw LID — this
    # is what lets the model match agent_runner's injected "Sender phone: X"
    # (always canonical) against this list. Showing the raw LID here was the
    # second root cause of an identity mix-up bug (the model could match an
    # @-mentioned LID against this list but never the real sender's phone).
    assert "Sivan Itzkovitch (admin): 972528695501" in block
    assert "Roni: 6541369471061" in block  # not an admin — no suffix, no known_lid so stays raw


# ── Task 6: rename_participant + set_household tools ──────────────────────────

from app.tools.accounting_tools import get_accounting_tools
from app.tool_registry import ToolRegistry


def _make_registry():
    registry = ToolRegistry()
    registry.register(get_accounting_tools())
    return registry


@pytest.mark.asyncio
async def test_rename_participant_sets_admin_name(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran W.", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "rename_participant",
        {"phone": "972501111111", "name": "Eran"},
        group_jid="123@g.us",
        sender="admin@s.whatsapp.net",
        is_admin=True,
        db=db,
    )
    assert "renamed" in result.lower() or "eran" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.admin_name == "Eran"


@pytest.mark.asyncio
async def test_rename_participant_rejects_non_admin(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "rename_participant",
        {"phone": "972501111111", "name": "X"},
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        db=db,
    )
    assert "admin" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.admin_name is None


@pytest.mark.asyncio
async def test_set_household_marks_participant(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "set_household",
        {"phone": "972501111111", "is_household": True},
        group_jid="123@g.us",
        sender="admin@s.whatsapp.net",
        is_admin=True,
        db=db,
    )
    assert "household" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.is_household is True


# ── Task 7: DB-based accounting helpers ───────────────────────────────────────

from app.tools.accounting_tools import _household_phones_from_db, _phone_to_name_from_db


def test_household_phones_from_db(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972502222222",
                             push_name="Sivan", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", is_household=False, status="active"))
    db.commit()

    phones = _household_phones_from_db(db, "123@g.us")
    assert phones == {"972501111111", "972502222222"}


def test_phone_to_name_from_db_household_maps_to_parents(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             admin_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", status="active"))
    db.commit()

    names = _phone_to_name_from_db(db, "123@g.us")
    assert names["972501111111"] == "Parents"
    assert names["972503333333"] == "Eden"


def test_phone_to_name_from_db_admin_name_priority(db):
    seed_blueprint(db, id="fa", display_name="FA")
    _seed_group_shared(db, "123@g.us", blueprint_id="fa")
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran W.", admin_name="Eran", status="active"))
    db.commit()

    names = _phone_to_name_from_db(db, "123@g.us")
    assert names["972501111111"] == "Eran"
