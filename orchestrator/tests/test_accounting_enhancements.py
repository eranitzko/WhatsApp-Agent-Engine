"""Tests for accounting enhancements: access control, corrections, email, report formats."""

import json
import pytest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import (
    Blueprint, GroupRegistry, GroupParticipant, AdminNumbers,
    LedgerEntry, UserProfile, ReportFormat,
)
from app.agent.correction_queue import CorrectionQueue


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _seed(db, group_jid="123@g.us"):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid=group_jid, blueprint_id="fa"))
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()


def _add_participant(db, group_jid, phone, push_name=None, is_household=False):
    db.add(GroupParticipant(group_jid=group_jid, phone=phone,
                             push_name=push_name, is_household=is_household, status="active"))
    db.commit()


def _add_entry(db, group_jid, from_phone, to_phone, amount_ils,
               amount_settled_ils=Decimal("0"), description="test", tx_id=None):
    import uuid
    entry = LedgerEntry(
        transaction_id=tx_id or str(uuid.uuid4()),
        group_jid=group_jid,
        from_phone=from_phone,
        to_phone=to_phone,
        amount_ils=amount_ils,
        amount_settled_ils=amount_settled_ils,
        description=description,
        transaction_date=date.today(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.commit()
    return entry


class _CM:
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


# ── Task: save_email ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_email_creates_profile(db):
    _seed(db)
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["save_email"]["executor"](
            {"email": "eran@example.com"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    assert "eran@example.com" in result
    db.expire_all()
    profile = db.get(UserProfile, "972500000001")
    assert profile is not None
    assert profile.email == "eran@example.com"


@pytest.mark.asyncio
async def test_save_email_updates_existing(db):
    _seed(db)
    db.add(UserProfile(phone="972500000001", email="old@example.com"))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        await tools["save_email"]["executor"](
            {"email": "new@example.com"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    db.expire_all()
    assert db.get(UserProfile, "972500000001").email == "new@example.com"


# ── Task: access control ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_balance_non_admin_sees_own_only(db):
    _seed(db)
    _add_participant(db, "123@g.us", "972500000002", "Sivan")
    _add_participant(db, "123@g.us", "972500000003", "Eden")
    _add_entry(db, "123@g.us", "972500000002", "972500000001", Decimal("100"))
    _add_entry(db, "123@g.us", "972500000003", "972500000001", Decimal("50"))

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        # Non-admin Sivan queries — should see only their own balance
        result = await tools["get_balance"]["executor"](
            {"phone_a": "972500000003"},  # trying to ask for Eden's balance
            group_jid="123@g.us",
            sender="972500000002@s.whatsapp.net",  # Sivan is sender
            is_admin=False,
        )
    # Should show Sivan's (972500000002) balance, not Eden's (972500000003)
    assert "100.00 ILS" in result
    assert "972500000003" not in result


@pytest.mark.asyncio
async def test_get_history_non_admin_sees_own_only(db):
    _seed(db)
    _add_entry(db, "123@g.us", "A", "B", Decimal("100"), description="A owes B")
    _add_entry(db, "123@g.us", "C", "B", Decimal("200"), description="C owes B")

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["get_history"]["executor"](
            {},
            group_jid="123@g.us",
            sender="A@s.whatsapp.net",
            is_admin=False,
        )
    assert "A owes B" in result
    assert "C owes B" not in result


@pytest.mark.asyncio
async def test_get_history_admin_sees_all(db):
    _seed(db)
    _add_entry(db, "123@g.us", "A", "B", Decimal("100"), description="A owes B")
    _add_entry(db, "123@g.us", "C", "B", Decimal("200"), description="C owes B")

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["get_history"]["executor"](
            {},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    assert "A owes B" in result
    assert "C owes B" in result


# ── Task: correct_transaction ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_correct_transaction_non_admin_rejected(db):
    _seed(db)
    entry = _add_entry(db, "123@g.us", "A", "B", Decimal("100"), tx_id="txabc12345")

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["correct_transaction"]["executor"](
            {"transaction_id": "txabc123", "new_amount_ils": 200},
            group_jid="123@g.us",
            sender="user@s.whatsapp.net",
            is_admin=False,
        )
    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_correct_transaction_blocks_if_settled(db):
    _seed(db)
    _add_entry(db, "123@g.us", "A", "B", Decimal("100"),
               amount_settled_ils=Decimal("50"), tx_id="txabc12345")

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["correct_transaction"]["executor"](
            {"transaction_id": "txabc123", "remove_phones": ["A"]},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    # Block fires before enqueue — settled check rejects the correction
    assert "settled" in result.lower()


@pytest.mark.asyncio
async def test_correct_transaction_enqueues_and_shows_diff(db):
    _seed(db)
    _add_entry(db, "123@g.us", "A", "B", Decimal("100"), tx_id="txabc12345")

    from app.agent.correction_queue import CorrectionQueue
    fresh_queue = CorrectionQueue()

    from app.agent.confirmation import ConfirmationStore
    store = ConfirmationStore()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.correction_queue", fresh_queue):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["correct_transaction"]["executor"](
            {"transaction_id": "txabc123", "new_amount_ils": 200},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
            confirmation_store=store,
        )

    assert "200" in result
    assert "token" in result.lower()
    pending = fresh_queue.peek_first("123@g.us")
    assert pending is not None
    assert pending.changes["new_amount_ils"] == 200


# ── Task: apply_correction ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_correction_updates_amount(db):
    _seed(db)
    entry = _add_entry(db, "123@g.us", "A", "B", Decimal("100"), tx_id="txabc12345")

    from app.agent.correction_queue import CorrectionQueue
    fresh_queue = CorrectionQueue()
    correction = fresh_queue.enqueue(
        group_jid="123@g.us",
        admin_phone="972500000001",
        transaction_id="txabc12345",
        changes={
            "transaction_id": "txabc12345",
            "payer": "B",
            "new_date": None,
            "new_amount_ils": 200,
            "add_phones": [],
            "remove_phones": [],
            "current_participants": ["A"],
        },
        description="Change amount to 200",
        max_slots=2,
    )

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.correction_queue", fresh_queue):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["apply_correction"]["executor"](
            {"token": correction.token, "admin_phone": "972500000001"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )

    assert "applied" in result.lower()
    db.expire_all()
    updated = db.get(LedgerEntry, entry.id)
    assert updated.amount_ils == Decimal("200")
    assert fresh_queue.peek_first("123@g.us") is None


@pytest.mark.asyncio
async def test_apply_correction_updates_date(db):
    _seed(db)
    entry = _add_entry(db, "123@g.us", "A", "B", Decimal("100"), tx_id="txabc12345")

    from app.agent.correction_queue import CorrectionQueue
    fresh_queue = CorrectionQueue()
    correction = fresh_queue.enqueue(
        group_jid="123@g.us",
        admin_phone="972500000001",
        transaction_id="txabc12345",
        changes={
            "transaction_id": "txabc12345",
            "payer": "B",
            "new_date": "2026-01-15",
            "new_amount_ils": None,
            "add_phones": [],
            "remove_phones": [],
            "current_participants": ["A"],
        },
        description="Change date to 2026-01-15",
        max_slots=2,
    )

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.correction_queue", fresh_queue):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        await tools["apply_correction"]["executor"](
            {"token": correction.token, "admin_phone": "972500000001"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )

    db.expire_all()
    updated = db.get(LedgerEntry, entry.id)
    assert updated.transaction_date == date(2026, 1, 15)


# ── Task: CorrectionQueue ─────────────────────────────────────────────────────

def test_correction_queue_one_per_admin():
    q = CorrectionQueue()
    changes = {"transaction_id": "x", "payer": "B", "new_date": None,
               "new_amount_ils": None, "add_phones": [], "remove_phones": [], "current_participants": []}
    q.enqueue("g1", "admin1", "tx1", changes, "desc", max_slots=3)
    result = q.enqueue("g1", "admin1", "tx2", changes, "desc2", max_slots=3)
    assert isinstance(result, str)
    assert "pending" in result.lower()


def test_correction_queue_capacity_limit():
    q = CorrectionQueue()
    changes = {"transaction_id": "x", "payer": "B", "new_date": None,
               "new_amount_ils": None, "add_phones": [], "remove_phones": [], "current_participants": []}
    q.enqueue("g1", "admin1", "tx1", changes, "desc", max_slots=2)
    q.enqueue("g1", "admin2", "tx2", changes, "desc", max_slots=2)
    result = q.enqueue("g1", "admin3", "tx3", changes, "desc", max_slots=2)
    assert isinstance(result, str)
    assert "full" in result.lower()


def test_correction_queue_fifo_order():
    q = CorrectionQueue()
    changes = {"transaction_id": "x", "payer": "B", "new_date": None,
               "new_amount_ils": None, "add_phones": [], "remove_phones": [], "current_participants": []}
    c1 = q.enqueue("g1", "admin1", "tx1", changes, "first", max_slots=5)
    c2 = q.enqueue("g1", "admin2", "tx2", changes, "second", max_slots=5)
    assert q.peek_first("g1").token == c1.token
    q.remove("g1", c1.token)
    assert q.peek_first("g1").token == c2.token


def test_correction_queue_remove():
    q = CorrectionQueue()
    changes = {"transaction_id": "x", "payer": "B", "new_date": None,
               "new_amount_ils": None, "add_phones": [], "remove_phones": [], "current_participants": []}
    c = q.enqueue("g1", "admin1", "tx1", changes, "desc", max_slots=3)
    assert q.remove("g1", c.token) is True
    assert q.peek_first("g1") is None


# ── Task: report formats ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_report_format(db):
    _seed(db)
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["create_report_format"]["executor"](
            {"name": "monthly", "language": "he", "grouping": "month",
             "sections": ["balances", "transactions"], "date_format": "DD/MM/YYYY",
             "currency_display": "₪", "include_settled": False, "sort_by": "date"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    assert "monthly" in result.lower()
    db.expire_all()
    row = db.query(ReportFormat).filter_by(group_jid="123@g.us", name="monthly").first()
    assert row is not None
    cfg = row.config()
    assert cfg["language"] == "he"
    assert cfg["grouping"] == "month"
    assert cfg["currency_display"] == "₪"
    assert cfg["include_settled"] is False


@pytest.mark.asyncio
async def test_create_report_format_non_admin_rejected(db):
    _seed(db)
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["create_report_format"]["executor"](
            {"name": "monthly"},
            group_jid="123@g.us",
            sender="user@s.whatsapp.net",
            is_admin=False,
        )
    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_list_report_formats(db):
    _seed(db)
    db.add(ReportFormat(
        group_jid="123@g.us", name="monthly",
        config_json=json.dumps({"language": "he", "grouping": "month",
                                "sections": ["balances"], "date_format": "DD/MM/YYYY",
                                "currency_display": "₪", "include_settled": False, "sort_by": "date"}),
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["list_report_formats"]["executor"](
            {},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    assert "monthly" in result


@pytest.mark.asyncio
async def test_delete_report_format(db):
    _seed(db)
    db.add(ReportFormat(
        group_jid="123@g.us", name="monthly",
        config_json=json.dumps({"language": "en", "grouping": "none",
                                "sections": ["transactions"], "date_format": "YYYY-MM-DD",
                                "currency_display": "ILS", "include_settled": True, "sort_by": "date"}),
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["delete_report_format"]["executor"](
            {"name": "monthly"},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=True,
        )
    assert "deleted" in result.lower()
    db.expire_all()
    assert db.query(ReportFormat).filter_by(group_jid="123@g.us", name="monthly").first() is None


