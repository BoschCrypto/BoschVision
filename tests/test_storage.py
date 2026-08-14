import pytest

from hf_trading_bot.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    yield s
    s.close()


def _trade(storage, symbol, side, traded_at, **kw):
    storage.record_trade(
        symbol=symbol, side=side, qty=1.0, strategy_key="momentum_90d",
        status="filled", traded_at=traded_at, **kw
    )


def test_day_trades_in_window_counts_same_day_round_trip(storage):
    _trade(storage, "NVDA", "buy", "2026-08-12T14:00:00+00:00")
    _trade(storage, "NVDA", "sell", "2026-08-12T19:00:00+00:00")
    assert storage.day_trades_in_window("2026-08-06", "2026-08-12") == 1


def test_day_trades_in_window_ignores_overnight_hold(storage):
    _trade(storage, "NVDA", "buy", "2026-08-11T14:00:00+00:00")
    _trade(storage, "NVDA", "sell", "2026-08-12T19:00:00+00:00")
    assert storage.day_trades_in_window("2026-08-06", "2026-08-12") == 0


def test_day_trades_in_window_counts_per_symbol(storage):
    for sym in ("NVDA", "QQQ", "VGT"):
        _trade(storage, sym, "buy", "2026-08-12T14:00:00+00:00")
        _trade(storage, sym, "sell", "2026-08-12T19:00:00+00:00")
    assert storage.day_trades_in_window("2026-08-06", "2026-08-12") == 3


def test_day_trades_in_window_excludes_trades_outside_window(storage):
    _trade(storage, "NVDA", "buy", "2026-08-03T14:00:00+00:00")
    _trade(storage, "NVDA", "sell", "2026-08-03T19:00:00+00:00")
    assert storage.day_trades_in_window("2026-08-06", "2026-08-12") == 0


def test_day_trades_in_window_buy_only_is_not_a_day_trade(storage):
    _trade(storage, "NVDA", "buy", "2026-08-12T14:00:00+00:00")
    assert storage.day_trades_in_window("2026-08-06", "2026-08-12") == 0


def test_max_day_trades_defaults_to_three(storage):
    assert storage.get_settings()["max_day_trades"] == 3


def test_kill_switch_defaults_to_on(storage):
    # Safety default: the bot starts paused and must be explicitly enabled.
    assert storage.get_settings()["kill_switch_active"] == 1


def test_sweep_result_round_trips(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=5,
        hit_rate_pct=21.0, median_excess_pts=-71.2, total_trades=680,
        window_start="2021-01-01", window_end="2026-08-14",
    )
    rows = storage.all_sweep_results()
    assert len(rows) == 1
    assert rows[0]["strategy_key"] == "momentum_90d"
    assert rows[0]["hit_rate_pct"] == 21.0
    assert rows[0]["median_excess_pts"] == -71.2


def test_latest_sweep_result_returns_none_when_never_tested(storage):
    assert storage.latest_sweep_result("momentum_90d") is None


def test_latest_sweep_result_returns_the_most_recent_run(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="momentum_90d", symbols_tested=24, wins=5,
        hit_rate_pct=21.0, median_excess_pts=-71.2, total_trades=680,
        window_start="2021-01-01",
    )
    storage.record_sweep_result(
        run_id="run2", strategy_key="momentum_90d", symbols_tested=24, wins=14,
        hit_rate_pct=58.0, median_excess_pts=4.5, total_trades=690,
        window_start="2021-01-01",
    )
    latest = storage.latest_sweep_result("momentum_90d")
    assert latest["run_id"] == "run2"
    assert latest["hit_rate_pct"] == 58.0


def test_all_sweep_results_orders_newest_first(storage):
    storage.record_sweep_result(
        run_id="run1", strategy_key="sma_crossover", symbols_tested=24, wins=6,
        hit_rate_pct=25.0, median_excess_pts=-51.0, total_trades=360,
        window_start="2021-01-01",
    )
    storage.record_sweep_result(
        run_id="run2", strategy_key="macd_crossover", symbols_tested=24, wins=7,
        hit_rate_pct=29.0, median_excess_pts=-35.6, total_trades=1294,
        window_start="2021-01-01",
    )
    rows = storage.all_sweep_results()
    assert rows[0]["run_id"] == "run2"
