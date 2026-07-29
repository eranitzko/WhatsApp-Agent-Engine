from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.tools.accounting_fifo import DebtLeg, SettlementResult, apply_payment, net_pair


def _leg(id: str, amount: float, settled: float = 0.0, days_ago: int = 0) -> DebtLeg:
    return DebtLeg(
        id=id,
        amount_ils=Decimal(str(amount)),
        amount_settled_ils=Decimal(str(settled)),
        transaction_date=date.today() - timedelta(days=days_ago),
    )


def test_full_settlement_single_debt():
    result = apply_payment(Decimal("100"), [_leg("a", 100)])
    assert result.settlements == [("a", Decimal("100"))]
    assert result.updated_legs == [("a", Decimal("100"))]
    assert result.leftover == Decimal("0")


def test_partial_settlement_single_debt():
    result = apply_payment(Decimal("60"), [_leg("a", 100)])
    assert result.settlements == [("a", Decimal("60"))]
    assert result.updated_legs == [("a", Decimal("60"))]
    assert result.leftover == Decimal("0")


def test_fifo_oldest_settled_first():
    # debts ordered oldest-first (days_ago=5 then days_ago=1)
    debts = [_leg("old", 100, days_ago=5), _leg("new", 100, days_ago=1)]
    result = apply_payment(Decimal("120"), debts)
    assert ("old", Decimal("100")) in result.settlements
    assert ("new", Decimal("20")) in result.settlements
    assert result.leftover == Decimal("0")


def test_payment_exceeds_all_debts_leftover():
    debts = [_leg("a", 50), _leg("b", 30)]
    result = apply_payment(Decimal("100"), debts)
    assert result.leftover == Decimal("20")


def test_partial_already_settled_debt():
    result = apply_payment(Decimal("40"), [_leg("a", 100, settled=60)])
    assert result.settlements == [("a", Decimal("40"))]
    assert result.updated_legs == [("a", Decimal("100"))]
    assert result.leftover == Decimal("0")


def test_empty_debts_returns_full_leftover():
    result = apply_payment(Decimal("100"), [])
    assert result.settlements == []
    assert result.updated_legs == []
    assert result.leftover == Decimal("100")


def test_zero_payment_does_nothing():
    result = apply_payment(Decimal("0"), [_leg("a", 100)])
    assert result.settlements == []
    assert result.leftover == Decimal("0")


def test_fully_settled_debt_is_skipped():
    debts = [_leg("done", 100, settled=100), _leg("open", 50)]
    result = apply_payment(Decimal("50"), debts)
    assert all(d != "done" for d, _ in result.settlements)
    assert ("open", Decimal("50")) in result.settlements


def test_payment_runs_out_mid_list_partial_settles_last_leg():
    # 150 payment against two 100 debts: first fully settled, second partially settled
    debts = [_leg("first", 100, days_ago=5), _leg("second", 100, days_ago=1)]
    result = apply_payment(Decimal("150"), debts)
    assert ("first", Decimal("100")) in result.settlements
    assert ("second", Decimal("50")) in result.settlements
    assert result.leftover == Decimal("0")
    # second debt only partially settled
    second_updated = next(amt for leg_id, amt in result.updated_legs if leg_id == "second")
    assert second_updated == Decimal("50")


def test_debtleg_remaining_ils_guards_none_settled():
    """Regression: DebtLeg.remaining_ils must default a None settled amount
    to zero, matching LedgerEntry.remaining_ils's guard — otherwise this
    diverges into a TypeError the moment amount_settled_ils is ever None,
    while every other copy of this same calculation silently treats it as 0."""
    leg = DebtLeg(id="x", amount_ils=Decimal("100"), amount_settled_ils=None, transaction_date=date.today())
    assert leg.remaining_ils == Decimal("100")


def test_split_evenly_rounds_half_up():
    """Regression: splitting an amount that lands exactly on a half-cent
    must round up (matching split_tools.py's existing explicit choice) —
    previously two of the three call sites defaulted to ROUND_HALF_EVEN
    instead, silently landing on a different per-person cent value for the
    identical split depending which tool computed it."""
    from app.tools.accounting_fifo import split_evenly

    # 100.005 / 1 landing exactly on a half-cent boundary after quantization:
    # use an amount/count combination that produces exactly x.xx5.
    result = split_evenly(Decimal("100.005"), 1)
    assert result == [Decimal("100.01")]  # ROUND_HALF_UP, not ROUND_HALF_EVEN (100.00)


def test_split_evenly_returns_one_share_per_person():
    from app.tools.accounting_fifo import split_evenly

    result = split_evenly(Decimal("100"), 4)
    assert result == [Decimal("25.00")] * 4


def test_net_pair_a_nets_positive():
    """a owes more than b owes back -> a is the debtor, amount is the positive diff."""
    result = net_pair("alice", "bob", Decimal("30"))
    assert result == ("alice", "bob", Decimal("30"))


def test_net_pair_b_nets_positive():
    """Negative net -> b is actually the debtor, and the amount is reported positive."""
    result = net_pair("alice", "bob", Decimal("-30"))
    assert result == ("bob", "alice", Decimal("30"))


def test_net_pair_exact_zero_returns_none():
    """Fully offset -> nothing to show, not a $0 line."""
    assert net_pair("alice", "bob", Decimal("0")) is None


def test_net_pair_computed_from_two_directed_amounts():
    """Typical caller shape: subtract two raw directed amounts before calling."""
    a_owes_b = Decimal("50")
    b_owes_a = Decimal("20")
    result = net_pair("alice", "bob", a_owes_b - b_owes_a)
    assert result == ("alice", "bob", Decimal("30"))


def test_net_pair_two_directed_amounts_exactly_offset():
    """Two raw directed amounts that are equal net to exactly zero -> None."""
    a_owes_b = Decimal("40")
    b_owes_a = Decimal("40")
    assert net_pair("alice", "bob", a_owes_b - b_owes_a) is None
