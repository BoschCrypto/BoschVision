"""Execution bridge: sizing, guards, and placement. Offline, fake broker."""
import pytest

from hf_trading_bot.broker.base import Account, Order, Position
from hf_trading_bot.execution import (
    DEFAULT_POSITION_PCT,
    ExecutionError,
    build_proposal,
    is_paper,
    place,
    validate,
)
from hf_trading_bot.storage import Storage


class FakeBroker:
    """Records the last order; never touches a network or real account."""
    def __init__(self):
        self.placed = None

    def place_order(self, symbol, qty, side, order_type="market", stop_price=None, limit_price=None):
        self.placed = dict(symbol=symbol, qty=qty, side=side, type=order_type, stop=stop_price)
        return Order(id="fake-1", symbol=symbol, side=side, qty=qty, type=order_type,
                     status="filled", stop_price=stop_price)


# --- sizing -----------------------------------------------------------------

def test_buy_sized_to_pct_of_equity():
    p = build_proposal(symbol="aapl", side="buy", price=200, equity=10_000,
                       buying_power=10_000, max_position_pct=20, position_pct=4)
    assert p.symbol == "AAPL"
    assert p.est_notional == pytest.approx(400)
    assert p.qty == pytest.approx(2)


def test_buy_clamped_by_per_symbol_cap():
    # ask 50% but the cap is 20% of equity → $2,000
    p = build_proposal(symbol="AAPL", side="buy", price=200, equity=10_000,
                       buying_power=10_000, max_position_pct=20, position_pct=50)
    assert p.est_notional == pytest.approx(2000)


def test_cap_counts_existing_holding():
    # cap is $2,000; already holding $1,500 → only $500 more
    p = build_proposal(symbol="AAPL", side="buy", price=100, equity=10_000,
                       buying_power=10_000, max_position_pct=20, position_pct=50,
                       held_value=1500)
    assert p.est_notional == pytest.approx(500)


def test_buy_clamped_by_buying_power():
    p = build_proposal(symbol="AAPL", side="buy", price=200, equity=10_000,
                       buying_power=300, max_position_pct=20, position_pct=10)
    assert p.est_notional == pytest.approx(300)


def test_buy_uses_default_pct_when_unspecified():
    p = build_proposal(symbol="AAPL", side="buy", price=100, equity=10_000,
                       buying_power=10_000, max_position_pct=20, position_pct=None)
    assert p.est_notional == pytest.approx(10_000 * DEFAULT_POSITION_PCT / 100)


def test_sell_closes_held_position():
    p = build_proposal(symbol="AAPL", side="sell", price=210, equity=10_000,
                       buying_power=0, max_position_pct=20, held_qty=2)
    assert p.side == "sell" and p.qty == pytest.approx(2)
    assert p.est_notional == pytest.approx(420)


def test_sell_without_position_is_an_error():
    with pytest.raises(ExecutionError, match="no AAPL position"):
        build_proposal(symbol="AAPL", side="sell", price=200, equity=10_000,
                       buying_power=0, max_position_pct=20, held_qty=0)


def test_zero_price_is_an_error():
    with pytest.raises(ExecutionError, match="no valid price"):
        build_proposal(symbol="AAPL", side="buy", price=0, equity=10_000,
                       buying_power=10_000, max_position_pct=20)


# --- guards -----------------------------------------------------------------

def _clean_buy():
    return build_proposal(symbol="AAPL", side="buy", price=200, equity=10_000,
                          buying_power=10_000, max_position_pct=20, position_pct=4)


def test_kill_switch_blocks_execution():
    ok, reasons = validate(_clean_buy(), broker_name="paper", kill_switch=True,
                           buying_power=10_000)
    assert not ok and any("kill switch" in r for r in reasons)


def test_real_money_broker_refused():
    ok, reasons = validate(_clean_buy(), broker_name="robinhood", kill_switch=False,
                           buying_power=10_000)
    assert not ok and any("paper-only" in r for r in reasons)


def test_paper_brokers_are_allowed():
    assert is_paper("paper") and is_paper("alpaca")
    assert not is_paper("robinhood")
    for name in ("paper", "alpaca"):
        ok, _ = validate(_clean_buy(), broker_name=name, kill_switch=False, buying_power=10_000)
        assert ok


def test_notional_below_minimum_refused():
    tiny = build_proposal(symbol="AAPL", side="buy", price=200, equity=10,
                          buying_power=10, max_position_pct=1, position_pct=1)
    ok, reasons = validate(tiny, broker_name="paper", kill_switch=False, buying_power=10)
    assert not ok and any("minimum" in r for r in reasons)


def test_buy_exceeding_buying_power_refused():
    p = build_proposal(symbol="AAPL", side="buy", price=200, equity=100_000,
                       buying_power=100_000, max_position_pct=90, position_pct=50)
    ok, reasons = validate(p, broker_name="paper", kill_switch=False, buying_power=100)
    assert not ok and any("buying power" in r for r in reasons)


def test_clean_proposal_passes_all_guards():
    ok, reasons = validate(_clean_buy(), broker_name="alpaca", kill_switch=False,
                           buying_power=10_000)
    assert ok and reasons == []


# --- placement --------------------------------------------------------------

def test_place_sends_to_broker_with_stop():
    b = FakeBroker()
    p = build_proposal(symbol="AAPL", side="buy", price=200, equity=10_000,
                       buying_power=10_000, max_position_pct=20, position_pct=4,
                       stop_price=180)
    order = place(b, p)
    assert order.status == "filled"
    assert b.placed["symbol"] == "AAPL" and b.placed["stop"] == 180


# --- storage round-trip -----------------------------------------------------

@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "exec.db"))
    yield s
    s.close()


def test_proposal_lifecycle_in_storage(storage):
    pid = storage.record_order_proposal(symbol="AAPL", side="buy", qty=2, est_price=200,
                                        est_notional=400, broker="paper", decision_id=7)
    assert len(storage.pending_order_proposals()) == 1
    storage.update_order_proposal(pid, status="filled", broker_order_id="paper-1",
                                  detail="placed via paper")
    assert storage.pending_order_proposals() == []
    r = storage.get_order_proposal(pid)
    assert r["status"] == "filled" and r["broker_order_id"] == "paper-1"
    assert r["decision_id"] == 7
