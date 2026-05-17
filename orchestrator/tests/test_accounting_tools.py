import inspect
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import LedgerEntry, ScheduledMessage
from app.tools.accounting_tools import get_accounting_tools

EXPECTED_TOOLS = [
    "record_transaction", "record_payment", "get_balance",
    "get_history", "export_ledger", "set_reminder",
    "rename_participant", "set_household",
]


class _CM:
    """Wrap a plain SQLAlchemy Session as a context manager for patching SessionLocal."""
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        pass


def test_get_accounting_tools_returns_all_eight():
    tools = get_accounting_tools()
    assert set(tools.keys()) == set(EXPECTED_TOOLS)


def test_each_tool_has_schema_and_executor():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name


def test_each_schema_has_required_keys():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        missing = {"name", "description", "input_schema"} - entry["schema"].keys()
        assert not missing, f"{name}: schema missing {missing}"


def test_all_executors_are_async():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        assert inspect.iscoroutinefunction(entry["executor"]), f"{name} executor is not async"


@pytest.mark.asyncio
async def test_record_transaction_creates_legs_for_each_participant(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("300"))):
        tools = get_accounting_tools()
        result = await tools["record_transaction"]["executor"](
            {
                "payer_phone": "972500000001",
                "participant_phones": ["972500000002", "972500000003"],
                "amount": 300,
                "currency": "ILS",
                "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    # Should create 2 legs (one per participant)
    legs = db.query(LedgerEntry).all()
    assert len(legs) == 2
    assert all(leg.to_phone == "972500000001" for leg in legs)
    assert all(leg.amount_ils == Decimal("150.00") for leg in legs)
    assert "recorded" in result.lower() or "972500000001" in result


@pytest.mark.asyncio
async def test_get_balance_settled_up(db):
    now = datetime.now(timezone.utc)
    db.add(LedgerEntry(
        transaction_id="t1", group_jid="123@g.us",
        from_phone="A", to_phone="B",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("100"),
        description="test", transaction_date=date.today(), created_at=now,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["get_balance"]["executor"](
            {"phone_a": "A", "phone_b": "B"},
            group_jid="123@g.us",
            sender="A@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "settled" in result.lower()


@pytest.mark.asyncio
async def test_get_balance_shows_debt(db):
    now = datetime.now(timezone.utc)
    db.add(LedgerEntry(
        transaction_id="t2", group_jid="123@g.us",
        from_phone="A", to_phone="B",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        description="test", transaction_date=date.today(), created_at=now,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["get_balance"]["executor"](
            {"phone_a": "A", "phone_b": "B"},
            group_jid="123@g.us",
            sender="A@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "100.00" in result
    assert "A" in result


@pytest.mark.asyncio
async def test_set_reminder_creates_scheduled_message(db):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["set_reminder"]["executor"](
            {"message": "pay Dana", "send_at": future},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "reminder set" in result.lower()
    msg = db.query(ScheduledMessage).first()
    assert msg is not None
    assert msg.message == "pay Dana"
    assert msg.to_phone == "972500000001"


@pytest.mark.asyncio
async def test_set_reminder_rejects_past_datetime(db):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["set_reminder"]["executor"](
            {"message": "too late", "send_at": past},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "future" in result.lower()
