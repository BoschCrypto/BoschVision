from datetime import datetime, timezone

import pytest

from hf_trading_bot.broker.base import Account, Broker, Order, Position
from hf_trading_bot.data.bars import Bar
from hf_trading_bot.engine import run_strategy_cycle
from hf_trading_bot.storage import Storage

TODAY = datetime.now(timezone.utc).date().isoformat()


class FakeBroker(Broker):
    """Records orders instead of placing them; serves scripted bars."""

    def __init__(self, bars, positions=None, equity=200.0, cash=200.0):
        self._bars = bars
        self._positions = positions or []
        self._equity = equity
        self._cash = cash
        self.orders = []

    def get_account(self):
        return Account(equity=self._equity, last_equity=self._equity, cash=self._cash, buying_power=self._cash)

    def get_positions(self):
        return list(self._positions)

    def get_open_orders(self):
        return []

    def place_order(self, symbol, qty, side, order_type="market", stop_price=None, limit_price=None):
        self.orders.append({"symbol": symbol, "qty": qty, "side": side, "stop_price": stop_price})
        return Order(id=f"fake-{len(self.orders)}", symbol=symbol, side=side, qty=qty,
                     type=order_type, status="filled", stop_price=stop_price)

    def cancel_order(self, order_id):
        return None

    def get_daily_bars(self, symbols, lookback_days=220):
        return {s: self._bars for s in symbols}


def make_bars(closes, low_override=None):
    bars = [
        Bar(t=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", o=c, h=c + 1, l=c - 1, c=c, v=1000)
        for i, c in enumerate(closes)
    ]
    if low_override is not None:
        last = bars[-1]
        bars[-1] = Bar(t=last.t, o=last.o, h=last.h, l=low_override, c=last.c, v=last.v)
    return bars


def entry_signal_bars():
    """Momentum crosses negative -> positive on the last bar (entry signal)."""
    return make_bars([100.0] * 92 + [90.0, 105.0])


def exit_signal_bars():
    """Momentum crosses positive -> negative on the last bar (exit signal)."""
    return make_bars([100.0] * 92 + [110.0, 95.0])


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "engine.db"))
    s.upsert_watchlist_symbol("NVDA", "momentum_90d", rank=0)
    s.set_kill_switch(False)
    yield s
    s.close()


def test_fractional_qty_is_not_floored_to_zero(storage):
    # $200 equity, ~$105 share price: whole-share flooring would have made
    # this 1 share or 0; risk-sizing should now produce a fractional qty.
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)
    storage.update_settings(risk_per_trade_pct=7, max_position_pct=45, max_open_positions=2)

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
    buys = [o for o in broker.orders if o["side"] == "buy"]
    assert len(buys) == 1
    assert 0 < buys[0]["qty"] < 1  # fractional — $90 cap on a $105 stock
    assert buys[0]["qty"] != int(buys[0]["qty"])


def test_buy_order_does_not_request_broker_side_stop_entry(storage):
    # The engine passes stop_price as metadata; RobinhoodBroker must not turn
    # that into a stop-entry order. Engine still records it for its own check.
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)
    run_strategy_cycle(broker, storage, dry_run=False)

    trades = storage.recent_open_entries()
    assert trades[0]["stop_price"] is not None


def test_pdt_guard_blocks_same_day_signal_exit_when_budget_exhausted(storage):
    position = Position("NVDA", 1.0, 100.0, 95.0, 95.0, -5.0, -0.05)
    broker = FakeBroker(exit_signal_bars(), positions=[position])
    # Position opened today -> selling today would be a day trade.
    storage.record_trade(
        symbol="NVDA", side="buy", qty=1.0, entry_price=100.0, strategy_key="momentum_90d",
        status="filled", stop_price=1.0, opened_at=f"{TODAY}T10:00:00+00:00",
    )
    # Budget already spent: 3 prior same-day round trips in the window.
    for sym in ("AAA", "BBB", "CCC"):
        for side in ("buy", "sell"):
            storage.record_trade(
                symbol=sym, side=side, qty=1.0, strategy_key="momentum_90d",
                status="filled", traded_at=f"{TODAY}T11:00:00+00:00",
            )

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
    assert [o for o in broker.orders if o["side"] == "sell"] == []
    assert any("PDT day-trade budget exhausted" in line for line in result.log)


def test_pdt_guard_allows_signal_exit_for_position_opened_earlier(storage):
    position = Position("NVDA", 1.0, 100.0, 95.0, 95.0, -5.0, -0.05)
    broker = FakeBroker(exit_signal_bars(), positions=[position])
    # Opened days ago -> not a day trade, so the budget is irrelevant.
    storage.record_trade(
        symbol="NVDA", side="buy", qty=1.0, entry_price=100.0, strategy_key="momentum_90d",
        status="filled", stop_price=1.0, opened_at="2026-01-02T10:00:00+00:00",
    )
    for sym in ("AAA", "BBB", "CCC"):
        for side in ("buy", "sell"):
            storage.record_trade(
                symbol=sym, side=side, qty=1.0, strategy_key="momentum_90d",
                status="filled", traded_at=f"{TODAY}T11:00:00+00:00",
            )

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
    assert len([o for o in broker.orders if o["side"] == "sell"]) == 1


def test_stop_loss_exit_always_executes_despite_pdt_budget(storage):
    # Capital protection outranks the day-trade budget: a breached stop sells
    # even when the PDT budget is fully spent and the position opened today.
    position = Position("NVDA", 1.0, 100.0, 90.0, 90.0, -10.0, -0.10)
    broker = FakeBroker(make_bars([100.0] * 94, low_override=80.0), positions=[position])
    storage.record_trade(
        symbol="NVDA", side="buy", qty=1.0, entry_price=100.0, strategy_key="momentum_90d",
        status="filled", stop_price=95.0, opened_at=f"{TODAY}T10:00:00+00:00",
    )
    for sym in ("AAA", "BBB", "CCC"):
        for side in ("buy", "sell"):
            storage.record_trade(
                symbol=sym, side=side, qty=1.0, strategy_key="momentum_90d",
                status="filled", traded_at=f"{TODAY}T11:00:00+00:00",
            )

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
    sells = [o for o in broker.orders if o["side"] == "sell"]
    assert len(sells) == 1
    assert any("STOP LOSS hit" in line for line in result.log)


def test_kill_switch_on_blocks_entries_but_cycle_succeeds(storage):
    storage.set_kill_switch(True)
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
    assert result.entries_blocked
    assert broker.orders == []


def test_live_run_refuses_a_strategy_with_a_failed_sweep(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=5,
        hit_rate_pct=21.0, median_excess_pts=-71.2, total_trades=680,
        window_start="2021-01-01",
    )
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert not result.ok
    assert "Refusing to trade live" in result.message
    assert "momentum_90d" in result.message
    assert broker.orders == []


def test_live_run_allows_override_of_a_failed_sweep(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=5,
        hit_rate_pct=21.0, median_excess_pts=-71.2, total_trades=680,
        window_start="2021-01-01",
    )
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=False, allow_failed_backtest=True)

    assert result.ok


def test_dry_run_is_not_blocked_by_a_failed_sweep(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=5,
        hit_rate_pct=21.0, median_excess_pts=-71.2, total_trades=680,
        window_start="2021-01-01",
    )
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=True)

    assert result.ok


def test_live_run_is_not_blocked_by_an_untested_strategy(storage):
    # No sweep result recorded at all — the guard is about known-failing
    # strategies, not ones that simply haven't been tested yet.
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok


def test_live_run_is_not_blocked_by_a_passing_sweep(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=15,
        hit_rate_pct=62.5, median_excess_pts=5.0, total_trades=680,
        window_start="2021-01-01",
    )
    broker = FakeBroker(entry_signal_bars(), equity=200.0, cash=200.0)

    result = run_strategy_cycle(broker, storage, dry_run=False)

    assert result.ok
