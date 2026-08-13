from hf_trading_bot.backtest import replay, stats
from hf_trading_bot.data.bars import Bar


def make_bars(closes: list[float]) -> list[Bar]:
    return [Bar(t=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", o=c, h=c, l=c, c=c, v=1_000) for i, c in enumerate(closes)]


def test_replay_produces_no_trades_on_flat_series():
    bars = make_bars([100] * 30)
    trades = replay(bars, "sma_crossover", {"fast": 3, "slow": 10})
    assert trades == []


def test_replay_sma_crossover_captures_uptrend():
    closes = [100] * 10 + [101 + i * 2 for i in range(20)]
    bars = make_bars(closes)
    trades = replay(bars, "sma_crossover", {"fast": 3, "slow": 8})
    assert len(trades) >= 1
    assert trades[0].entry_price > 0


def test_stats_on_no_trades_returns_none_fields():
    s = stats([], years=1.0)
    assert s.total_trades == 0
    assert s.win_rate is None
    # No trades means the equity curve never moves off 1.0, so CAGR is 0%,
    # not undefined — win_rate/max_drawdown are the ones that go None.
    assert s.cagr == 0.0
    assert s.max_drawdown is None


def test_stats_computes_win_rate_and_cagr_for_all_winning_trades():
    from hf_trading_bot.backtest import ReplayTrade

    trades = [
        ReplayTrade("2026-01-01", 100, "2026-02-01", 110, 1000, 10.0, 30, False),
        ReplayTrade("2026-03-01", 100, "2026-04-01", 120, 2000, 20.0, 30, False),
    ]
    s = stats(trades, years=1.0)
    assert s.total_trades == 2
    assert s.wins == 2
    assert s.win_rate == 100.0
    assert s.cagr is not None and s.cagr > 0
