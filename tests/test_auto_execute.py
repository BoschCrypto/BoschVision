"""Guards specific to unattended (auto-execute) order placement."""
import pytest

from hf_trading_bot.execution import ProposedOrder, auto_execute_blockers


def _p(notional=100.0, symbol="AAPL"):
    return ProposedOrder(symbol=symbol, side="buy", qty=1, est_price=notional,
                         est_notional=notional)


def test_paper_broker_under_ceiling_is_allowed():
    assert auto_execute_blockers(_p(100), broker_name="paper", max_notional=500) == []
    assert auto_execute_blockers(_p(100), broker_name="alpaca", max_notional=500) == []


def test_live_broker_is_always_refused():
    # No allow_live escape hatch exists for unattended placement, by design.
    blockers = auto_execute_blockers(_p(10), broker_name="robinhood", max_notional=10_000)
    assert blockers and "paper-only" in blockers[0]


def test_notional_ceiling_is_enforced():
    blockers = auto_execute_blockers(_p(750), broker_name="paper", max_notional=500)
    assert blockers and "ceiling" in blockers[0]


def test_at_the_ceiling_is_allowed():
    assert auto_execute_blockers(_p(500), broker_name="paper", max_notional=500) == []


def test_both_blockers_reported_together():
    blockers = auto_execute_blockers(_p(9_999), broker_name="robinhood", max_notional=500)
    assert len(blockers) == 2
