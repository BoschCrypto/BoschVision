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
