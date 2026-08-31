import inspect
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import LedgerEntry, ScheduledMessage
from app.tools.accounting_tools import get_accounting_tools
from tests.conftest import SessionCM

EXPECTED_TOOLS = [
    "record_expense", "record_payment", "get_balance", "get_debt_summary",
    "get_history", "set_reminder", "list_reminders", "cancel_reminder",
    "set_report_email", "rename_participant", "list_participants",
    "get_transaction", "correct_transaction", "commit_correction",
    "create_report_format", "list_report_formats", "delete_report_format",
    "resend_confirmation",
]


def test_get_accounting_tools_returns_expected_set():
    tools = get_accounting_tools()
    assert set(tools.keys()) == set(EXPECTED_TOOLS)


def test_list_participants_description_does_not_reference_nonexistent_tool():
    """set_household was never implemented as a chat tool — GroupParticipant
    .is_household has no write path today — so the description must not
    send the agent looking for a tool that doesn't exist in the registry."""
    tools = get_accounting_tools()
    description = tools["list_participants"]["schema"]["description"]
    assert "set_household" not in description


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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)), \
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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
async def test_get_balance_combines_shared_ledger_pool_when_asked_from_a_different_group(db):
    """Regression: a household member's 'list my balances' view must combine
    a joint-ledger pool's separate debts into one 'Parents' line even when
    asked from a THIRD group (e.g. the debtor's own personal group) — not
    just when asked from the shared group itself. The previous
    _joint_pool_phones_from_db(group_jid)-based bucketing only ever saw a
    pool when group_jid WAS the shared group; get_joint_pool(partner) is
    cross-group and must be used instead."""
    import app.tools.accounting_tools as at_module
    from app.accounting.account_service import AccountService
    from app.db.models import GroupParticipant, GroupRegistry, UserProfile
    from tests.conftest import seed_blueprint, seed_group

    seed_blueprint(db, id="family_accounting", display_name="FA")

    # The shared parents' group — Eran (dad) and Sivan (mom) pool together.
    seed_group(db, "parents_shared@g.us", blueprint_id="family_accounting",
               group_type="shared", shared_ledger=True)
    db.add(GroupParticipant(group_jid="parents_shared@g.us", phone="eran_lid", status="active"))
    db.add(GroupParticipant(group_jid="parents_shared@g.us", phone="sivan_lid", status="active"))
    db.add(UserProfile(phone="972500000001", display_name="Eran", known_lid="eran_lid"))
    db.add(UserProfile(phone="972500000002", display_name="Sivan", known_lid="sivan_lid"))

    # Roni's OWN, unrelated personal group — this is where she's asking from.
    seed_group(db, "roni_own@g.us", blueprint_id="family_accounting", group_type="personal")

    db.add(LedgerEntry(
        transaction_id="t1", group_jid="roni_own@g.us",
        from_phone="972500000003", to_phone="972500000001",
        amount_ils=Decimal("500"), amount_settled_ils=Decimal("0"),
        description="shoes", transaction_date=date.today(),
    ))
    db.add(LedgerEntry(
        transaction_id="t2", group_jid="roni_own@g.us",
        from_phone="972500000003", to_phone="972500000002",
        amount_ils=Decimal("350"), amount_settled_ils=Decimal("0"),
        description="clothes", transaction_date=date.today(),
    ))
    db.commit()

    at_module.set_account_service(AccountService())
    try:
        with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
            tools = get_accounting_tools()
            result = await tools["get_balance"]["executor"](
                {"phone_a": "972500000003"},
                group_jid="roni_own@g.us",
                sender="972500000003@s.whatsapp.net",
                is_admin=True,
                confirmation_store=None,
            )
    finally:
        at_module.set_account_service(None)

    assert "Parents" in result
    assert "850.00" in result
    # Must NOT list Eran and Sivan as two separate lines.
    assert result.count("owe") == 1


@pytest.mark.asyncio
async def test_set_reminder_creates_scheduled_message(db):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)), \
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
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

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        tools = get_accounting_tools()
        result = await tools["list_participants"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "972501111111" in result
    assert "Eran" in result
    assert "972502222222" in result
    assert "Tal (override)" in result  # admin_name takes precedence
    assert "972503333333" not in result  # removed members excluded


@pytest.mark.asyncio
async def test_get_transaction_returns_detail(db):
    import uuid
    tx_id = str(uuid.uuid4())
    db.add(LedgerEntry(
        id=str(uuid.uuid4()),
        transaction_id=tx_id,
        group_jid="g@g.us",
        from_phone="972501111111",
        to_phone="972502222222",
        amount_ils=Decimal("150.00"),
        amount_settled_ils=Decimal("0"),
        description="Restaurant",
        transaction_date=date(2026, 5, 1),
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        tools = get_accounting_tools()
        result = await tools["get_transaction"]["executor"](
            {"transaction_id": tx_id[:8]},
            group_jid="g@g.us",
            sender="972501111111@s.whatsapp.net",
            is_admin=True,
        )
    assert "Restaurant" in result
    assert "150" in result
    assert "972501111111" in result


@pytest.mark.asyncio
async def test_get_transaction_admin_only(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        tools = get_accounting_tools()
        result = await tools["get_transaction"]["executor"](
            {"transaction_id": "any-prefix"},
            group_jid="g@g.us",
            sender="972501111111@s.whatsapp.net",
            is_admin=False,
        )
    assert "admin" in result.lower()


@pytest.mark.asyncio
async def test_get_debt_summary_shows_open_debts(db):
    import uuid
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import LedgerEntry
    from decimal import Decimal
    from datetime import date

    db.add(LedgerEntry(
        id=str(uuid.uuid4()),
        transaction_id=str(uuid.uuid4()),
        group_jid="g@g.us",
        from_phone="972502222222",
        to_phone="972501111111",
        amount_ils=Decimal("200.00"),
        amount_settled_ils=Decimal("0"),
        description="Groceries",
        transaction_date=date(2026, 4, 1),
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        tools = get_accounting_tools()
        result = await tools["get_debt_summary"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "200" in result
    assert "972502222222" in result or "972501111111" in result


@pytest.mark.asyncio
async def test_get_debt_summary_no_debts(db):
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        tools = get_accounting_tools()
        result = await tools["get_debt_summary"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "no open debts" in result.lower() or "no debts" in result.lower()


@pytest.mark.asyncio
async def test_get_debt_summary_nets_bilateral_debts(db):
    """Regression: A owes B 100 and B owes A 30 on separate entries must net
    to a single 'A owes B: 70' line, matching get_balance's netting - not
    two separate, contradictory lines."""
    from app.db.models import LedgerEntry
    from app.tools.accounting_tools import get_accounting_tools

    db.add(LedgerEntry(
        transaction_id="tx1", group_jid="123@g.us", entry_type="debt",
        from_phone="972501", to_phone="972502",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        transaction_date=date(2026, 7, 1),
    ))
    db.add(LedgerEntry(
        transaction_id="tx2", group_jid="123@g.us", entry_type="debt",
        from_phone="972502", to_phone="972501",
        amount_ils=Decimal("30"), amount_settled_ils=Decimal("0"),
        transaction_date=date(2026, 7, 2),
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=SessionCM(db)):
        result = await tools["get_debt_summary"]["executor"](
            {}, group_jid="123@g.us", is_admin=True, sender="972501@s.whatsapp.net",
        )

    assert "972501 owes 972502: ₪70.00" in result
    assert "972502 owes 972501" not in result
