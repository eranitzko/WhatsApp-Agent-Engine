import inspect
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import LedgerEntry, ScheduledMessage
from app.tools.accounting_tools import get_accounting_tools

EXPECTED_TOOLS = [
    "record_expense", "record_payment", "get_balance",
    "get_history", "set_reminder", "list_reminders", "cancel_reminder",
    "set_report_email", "rename_participant", "set_household", "list_participants",
    "correct_transaction", "commit_correction",
    "create_report_format", "list_report_formats", "delete_report_format",
]


class _CM:
    """Wrap a plain SQLAlchemy Session as a context manager for patching SessionLocal."""
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        pass


def test_get_accounting_tools_returns_expected_set():
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
    from app.agent.multi_confirmation import MultiConfirmationStore
    mcs = MultiConfirmationStore()
    mcs.set_sender(AsyncMock())

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("300"))):
        tools = get_accounting_tools()
        result = await tools["record_expense"]["executor"](
            {
                "payer_phone": "972500000001",
                "participant_phones": ["972500000002", "972500000003"],
                "amount": 300,
                "currency": "ILS",
                "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender == payer → participants must confirm
            is_admin=False,
            multi_confirmation_store=mcs,
        )
    # Participants (002, 003) must confirm → staged, not yet in DB
    assert "confirmation" in result.lower()
    pending = mcs.find_for_phone("123@g.us", "972500000002")
    assert pending is not None
    assert pending.commit_params["per_person_ils"] == "150.00"
    assert set(pending.commit_params["participant_phones"]) == {"972500000002", "972500000003"}


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
            is_admin=True,
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
            is_admin=True,
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


@pytest.mark.asyncio
async def test_record_transaction_uses_account_service_when_injected(db):
    """When AccountService is set, record_transaction delegates routing to it."""
    from unittest.mock import MagicMock
    import app.tools.accounting_tools as at_module

    mock_svc = MagicMock()
    mock_svc.process_transaction = AsyncMock(return_value="Confirmation sent to Tal.")
    at_module.set_account_service(mock_svc)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("100"))):
        tools = get_accounting_tools()
        result = await tools["record_expense"]["executor"](
            {
                "payer_phone": "972500000001",
                "participant_phones": ["972500000002"],
                "amount": 100,
                "currency": "ILS",
                "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            multi_confirmation_store=None,
        )

    mock_svc.process_transaction.assert_awaited_once()
    assert "Confirmation" in result or "recorded" in result.lower()

    at_module.set_account_service(None)  # clean up


def test_correct_transaction_has_step_label():
    tools = get_accounting_tools()
    desc = tools["correct_transaction"]["schema"]["description"]
    assert "Step 1 of 2" in desc
    assert "commit_correction" in desc


def test_commit_correction_has_step_label():
    tools = get_accounting_tools()
    desc = tools["commit_correction"]["schema"]["description"]
    assert "Step 2 of 2" in desc
    assert "correct_transaction" in desc


def test_record_expense_tool_exists():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    assert "record_expense" in tools
    assert "record_transaction" not in tools

def test_set_report_email_tool_exists():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    assert "set_report_email" in tools
    assert "save_email" not in tools


@pytest.mark.asyncio
async def test_list_reminders_returns_pending(db):
    import uuid
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    msg_id = str(uuid.uuid4())
    db.add(ScheduledMessage(
        id=msg_id,
        group_jid="g@g.us",
        to_phone="972501234567",
        message="Test reminder",
        send_at=future,
        sent=False,
        cancelled=False,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["list_reminders"]["executor"](
            {},
            group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net",
            is_admin=False,
        )
    assert "Test reminder" in result


@pytest.mark.asyncio
async def test_list_reminders_returns_no_pending_when_empty(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["list_reminders"]["executor"](
            {},
            group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net",
            is_admin=False,
        )
    assert "No pending reminders" in result


@pytest.mark.asyncio
async def test_cancel_reminder_marks_cancelled(db):
    import uuid
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    msg_id = str(uuid.uuid4())
    db.add(ScheduledMessage(
        id=msg_id,
        group_jid="g@g.us",
        to_phone="972501234567",
        message="Cancel me",
        send_at=future,
        sent=False,
        cancelled=False,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["cancel_reminder"]["executor"](
            {"reminder_id": msg_id[:6]},
            group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net",
            is_admin=False,
        )
    assert "cancel" in result.lower() or "reminder" in result.lower()
    db.expire_all()
    updated = db.get(ScheduledMessage, msg_id)
    assert updated.cancelled is True


@pytest.mark.asyncio
async def test_cancel_reminder_not_found(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["cancel_reminder"]["executor"](
            {"reminder_id": "zzzz"},
            group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net",
            is_admin=False,
        )
    assert "not found" in result.lower() or "no pending" in result.lower()


@pytest.mark.asyncio
async def test_cancel_reminder_short_prefix_rejected(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["cancel_reminder"]["executor"](
            {"reminder_id": "ab"},
            group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net",
            is_admin=False,
        )
    assert "4 characters" in result or "at least" in result.lower()


@pytest.mark.asyncio
async def test_list_participants_returns_group_members(db):
    from app.db.models import GroupParticipant

    db.add(GroupParticipant(
        group_jid="g@g.us", phone="972501111111",
        push_name="Eran", status="active",
    ))
    db.add(GroupParticipant(
        group_jid="g@g.us", phone="972502222222",
        admin_name="Tal (override)", push_name="Tal", status="active",
    ))
    db.add(GroupParticipant(
        group_jid="g@g.us", phone="972503333333",
        push_name="Removed Person", status="removed",
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        tools = get_accounting_tools()
        result = await tools["list_participants"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "972501111111" in result
    assert "Eran" in result
    assert "972502222222" in result
    assert "Tal (override)" in result  # admin_name takes precedence
    assert "972503333333" not in result  # removed members excluded
