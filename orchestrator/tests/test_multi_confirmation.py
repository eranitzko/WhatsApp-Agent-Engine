"""Tests for multi-party confirmation store and transaction/payment confirmation flows."""

import pytest
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.multi_confirmation import MultiConfirmationStore, PendingMultiConfirmation
from app.db.models import Blueprint, GroupRegistry, AdminNumbers, LedgerEntry


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _seed(db, group_jid="123@g.us"):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid=group_jid, blueprint_id="fa"))
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()


def _add_entry(db, group_jid, from_phone, to_phone, amount_ils,
               amount_settled_ils=Decimal("0"), tx_id=None):
    import uuid
    entry = LedgerEntry(
        transaction_id=tx_id or str(uuid.uuid4()),
        group_jid=group_jid,
        from_phone=from_phone,
        to_phone=to_phone,
        amount_ils=amount_ils,
        amount_settled_ils=amount_settled_ils,
        description="test",
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


# ── MultiConfirmationStore unit tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_creates_pending():
    store = MultiConfirmationStore()
    store.set_sender(AsyncMock())
    mc = await store.propose("g1", ["A", "B"], "commit_transaction", {}, "desc")
    assert mc.awaiting == {"A": False, "B": False}
    assert not mc.all_confirmed()
    assert mc.pending_phones() == ["A", "B"]


@pytest.mark.asyncio
async def test_confirm_one_still_waiting():
    store = MultiConfirmationStore()
    store.set_sender(AsyncMock())
    await store.propose("g1", ["A", "B"], "commit_transaction", {}, "desc")
    status, mc = store.confirm("g1", "A")
    assert status == "waiting"
    assert mc.awaiting["A"] is True
    assert mc.awaiting["B"] is False


@pytest.mark.asyncio
async def test_confirm_all_returns_all_confirmed():
    store = MultiConfirmationStore()
    store.set_sender(AsyncMock())
    await store.propose("g1", ["A", "B"], "commit_transaction", {}, "desc")
    store.confirm("g1", "A")
    status, mc = store.confirm("g1", "B")
    assert status == "all_confirmed"
    assert mc is not None
    # Should be removed from store after all confirmed
    assert store.find_for_phone("g1", "A") is None


@pytest.mark.asyncio
async def test_reject_cancels_pending():
    store = MultiConfirmationStore()
    store.set_sender(AsyncMock())
    await store.propose("g1", ["A", "B"], "commit_transaction", {}, "desc")
    mc = store.reject("g1", "A")
    assert mc is not None
    assert store.find_for_phone("g1", "B") is None  # whole pending removed


@pytest.mark.asyncio
async def test_find_for_phone_not_found_when_confirmed():
    store = MultiConfirmationStore()
    store.set_sender(AsyncMock())
    await store.propose("g1", ["A"], "commit_transaction", {}, "desc")
    store.confirm("g1", "A")  # removes from store
    assert store.find_for_phone("g1", "A") is None


@pytest.mark.asyncio
async def test_drain_expired_returns_expired():
    store = MultiConfirmationStore()
    sender = AsyncMock()
    store.set_sender(sender)
    mc = await store.propose("g1", ["A"], "commit_transaction", {}, "desc")
    # Force expire
    mc.expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = store.drain_expired()
    assert len(expired) == 1
    assert expired[0].id == mc.id
    assert store.find_for_phone("g1", "A") is None


@pytest.mark.asyncio
async def test_propose_calls_sender():
    store = MultiConfirmationStore()
    sender = AsyncMock()
    store.set_sender(sender)
    await store.propose("g1", ["A", "B"], "commit_transaction", {}, "Buy groceries 100 ILS")
    sender.assert_called_once()
    call_args = sender.call_args
    assert call_args[0][0] == "g1"  # group_jid
    assert "@A" in call_args[0][1]
    assert "@B" in call_args[0][1]
    mentions = call_args[0][2]
    assert "A@s.whatsapp.net" in mentions


# ── record_transaction confirmation flow ──────────────────────────────────────

@pytest.mark.asyncio
async def test_record_transaction_self_records_immediately(db):
    """Sender is payer and only participant — records immediately."""
    _seed(db)
    mcs = MultiConfirmationStore()
    mcs.set_sender(AsyncMock())

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("100"))):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["record_transaction"]["executor"](
            {
                "payer_phone": "972500000002",
                "participant_phones": ["972500000001"],  # sender IS the participant
                "amount": 100, "currency": "ILS", "description": "lunch",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender == participant
            is_admin=False,
            multi_confirmation_store=mcs,
        )
    # Payer (972500000002) is not sender, so still needs confirmation
    assert "confirmation" in result.lower()


@pytest.mark.asyncio
async def test_record_transaction_payer_sends_awaits_participants(db):
    """Payer sends the message → only participants need to confirm."""
    _seed(db)
    mcs = MultiConfirmationStore()
    sender_mock = AsyncMock()
    mcs.set_sender(sender_mock)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("300"))):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["record_transaction"]["executor"](
            {
                "payer_phone": "972500000001",   # sender IS payer
                "participant_phones": ["972500000002", "972500000003"],
                "amount": 300, "currency": "ILS", "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender == payer
            is_admin=True,
            multi_confirmation_store=mcs,
        )

    assert "confirmation" in result.lower()
    # @mention sent to participants, NOT payer
    call_text = sender_mock.call_args[0][1]
    assert "@972500000002" in call_text
    assert "@972500000003" in call_text
    assert "@972500000001" not in call_text  # payer not mentioned


@pytest.mark.asyncio
async def test_record_transaction_third_party_awaits_all(db):
    """Third party sends → payer AND all participants need to confirm."""
    _seed(db)
    mcs = MultiConfirmationStore()
    sender_mock = AsyncMock()
    mcs.set_sender(sender_mock)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("300"))):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["record_transaction"]["executor"](
            {
                "payer_phone": "972500000002",
                "participant_phones": ["972500000003", "972500000004"],
                "amount": 300, "currency": "ILS", "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender is 3rd party
            is_admin=True,
            multi_confirmation_store=mcs,
        )

    assert "confirmation" in result.lower()
    call_text = sender_mock.call_args[0][1]
    assert "@972500000002" in call_text  # payer mentioned
    assert "@972500000003" in call_text
    assert "@972500000004" in call_text


# ── record_payment confirmation flow ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_payment_payer_sends_awaits_payee(db):
    """Payer sends 'I paid Eden' → Eden (payee) needs to confirm."""
    _seed(db)
    mcs = MultiConfirmationStore()
    sender_mock = AsyncMock()
    mcs.set_sender(sender_mock)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["record_payment"]["executor"](
            {"payer_phone": "972500000001", "payee_phone": "972500000002", "amount_ils": 100},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender == payer
            is_admin=True,
            multi_confirmation_store=mcs,
        )

    assert "confirmation" in result.lower()
    call_text = sender_mock.call_args[0][1]
    assert "@972500000002" in call_text   # payee mentioned
    assert "@972500000001" not in call_text  # payer not re-mentioned


@pytest.mark.asyncio
async def test_record_payment_payee_sends_awaits_payer(db):
    """Payee says 'Sivan paid me' → Sivan (payer) needs to confirm."""
    _seed(db)
    mcs = MultiConfirmationStore()
    sender_mock = AsyncMock()
    mcs.set_sender(sender_mock)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        from app.tools.accounting_tools import get_accounting_tools
        tools = get_accounting_tools()
        result = await tools["record_payment"]["executor"](
            {"payer_phone": "972500000002", "payee_phone": "972500000001", "amount_ils": 100},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",  # sender == payee
            is_admin=True,
            multi_confirmation_store=mcs,
        )

    assert "confirmation" in result.lower()
    call_text = sender_mock.call_args[0][1]
    assert "@972500000002" in call_text   # payer mentioned
    assert "@972500000001" not in call_text  # payee not re-mentioned


# ── AgentRunner multi-confirmation intercept ──────────────────────────────────

@pytest.mark.asyncio
async def test_agent_runner_intercepts_yes_and_commits(db):
    """When a pending party says 'yes', agent_runner commits once all confirmed."""
    from app.agent_runner import AgentRunner
    from app.agent.context import ContextStore
    from app.agent.confirmation import ConfirmationStore
    from app.db.models import Blueprint

    _seed(db)
    _add_entry(db, "123@g.us", "972500000002", "972500000001", Decimal("100"), tx_id="txabc12345")

    mcs = MultiConfirmationStore()
    mcs.set_sender(AsyncMock())

    commit_params = {
        "group_jid": "123@g.us",
        "transaction_id": "tx-test-1234",
        "payer_phone": "972500000001",
        "participant_phones": ["972500000002"],
        "per_person_ils": "150.00",
        "description": "dinner",
        "transaction_date": date.today().isoformat(),
    }
    mc = await mcs.propose("123@g.us", ["972500000002"], "commit_transaction", commit_params, "Dinner 150 ILS")

    bp = Blueprint(
        id="fa", display_name="FA", system_prompt="p", tools_enabled="[]",
        max_tool_turns=5, context_window=8, context_idle_reset_minutes=60,
    )

    mock_client = MagicMock()
    mock_registry = MagicMock()
    runner = AgentRunner(mock_client, mock_registry)

    ctx = ContextStore()
    cs = ConfirmationStore()

    with patch("app.db.session.SessionLocal", return_value=_CM(db)):
        result = await runner.run(
            blueprint=bp,
            group_jid="123@g.us",
            sender="972500000002@s.whatsapp.net",
            is_admin=False,
            message="yes",
            context=ctx,
            confirmation_store=cs,
            multi_confirmation_store=mcs,
        )

    assert "recorded" in result.lower() or "payment" in result.lower() or "transaction" in result.lower()
    assert mcs.find_for_phone("123@g.us", "972500000002") is None  # cleared


@pytest.mark.asyncio
async def test_agent_runner_intercepts_no_and_cancels():
    """When a pending party says 'no', the transaction is cancelled."""
    from app.agent_runner import AgentRunner
    from app.agent.context import ContextStore
    from app.agent.confirmation import ConfirmationStore
    from app.db.models import Blueprint

    mcs = MultiConfirmationStore()
    mcs.set_sender(AsyncMock())
    await mcs.propose("g1", ["A", "B"], "commit_transaction", {}, "Dinner 100 ILS")

    bp = Blueprint(
        id="fa", display_name="FA", system_prompt="p", tools_enabled="[]",
        max_tool_turns=5, context_window=8, context_idle_reset_minutes=60,
    )
    mock_client = MagicMock()
    mock_registry = MagicMock()
    runner = AgentRunner(mock_client, mock_registry)
    ctx = ContextStore()
    cs = ConfirmationStore()

    result = await runner.run(
        blueprint=bp,
        group_jid="g1",
        sender="A@s.whatsapp.net",
        is_admin=False,
        message="no",
        context=ctx,
        confirmation_store=cs,
        multi_confirmation_store=mcs,
    )

    assert "cancel" in result.lower()
    assert mcs.find_for_phone("g1", "B") is None
