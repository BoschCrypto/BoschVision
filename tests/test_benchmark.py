"""Benchmark comparison and statistical-honesty helpers. No network."""
import pytest

from hf_trading_bot.backtest import (
    MIN_MEANINGFUL_TRADES,
    ReplayTrade,
    buy_and_hold,
    significance_note,
    stats,
)
from hf_trading_bot.data.bars import Bar


def make_bars(closes):
    return [
        Bar(t=f"{2022 + i // 252}-{1 + (i % 252) // 21:02d}-{1 + (i % 21):02d}",
            o=c, h=c + 1, l=c - 1, c=c, v=1000)
        for i, c in enumerate(closes)
    ]


def test_buy_and_hold_total_return():
    bars = make_bars([100.0, 110.0, 150.0, 200.0])
    bh = buy_and_hold(bars, "TEST", years=1.0)
    assert bh.total_return_pct == pytest.approx(100.0)
    assert bh.cagr == pytest.approx(100.0)


def test_buy_and_hold_captures_intermediate_drawdown():
    # Ends flat, but fell 50% in the middle — the holder had to sit through it.
    bars = make_bars([100.0, 120.0, 60.0, 100.0])
    bh = buy_and_hold(bars, "TEST", years=1.0)
    assert bh.total_return_pct == pytest.approx(0.0)
    assert bh.max_drawdown == pytest.approx(-50.0)


def test_buy_and_hold_cagr_compounds_over_multiple_years():
    bars = make_bars([100.0, 200.0])
    bh = buy_and_hold(bars, "TEST", years=2.0)
    # doubling over 2 years ≈ 41.4%/yr
    assert bh.cagr == pytest.approx(41.42, abs=0.1)


def test_buy_and_hold_rejects_degenerate_input():
    assert buy_and_hold([], "T", 1.0) is None
    assert buy_and_hold(make_bars([100.0, 110.0]), "T", 0) is None


def _closed(pnl_pct, holding_days=10):
    return ReplayTrade("2022-01-01", 100.0, "2022-02-01", 100 * (1 + pnl_pct / 100),
                       0.0, pnl_pct, holding_days, False)


def test_total_return_compounds_closed_trades():
    s = stats([_closed(10), _closed(10)], years=1.0, total_bars=100)
    assert s.total_return_pct == pytest.approx(21.0)  # 1.1 * 1.1


def test_open_trade_excluded_from_returns():
    open_trade = ReplayTrade("2022-01-01", 100.0, None, None, None, None, None, True)
    s = stats([_closed(10), open_trade], years=1.0, total_bars=100)
    assert s.total_trades == 1
    assert s.total_return_pct == pytest.approx(10.0)


def test_time_in_market_reflects_holding_period():
    # 2 trades × 10 days held out of 100 bars = 20%
    s = stats([_closed(5, 10), _closed(5, 10)], years=1.0, total_bars=100)
    assert s.time_in_market_pct == pytest.approx(20.0)


def test_time_in_market_capped_at_100():
    s = stats([_closed(5, 200)], years=1.0, total_bars=100)
    assert s.time_in_market_pct == 100.0


def test_no_trades_yields_none_returns():
    s = stats([], years=1.0, total_bars=100)
    assert s.total_trades == 0
    assert s.total_return_pct is None


@pytest.mark.parametrize(
    "n,expected",
    [(0, "NO TRADES"), (5, "FAR too few"), (18, "Below the"), (45, "Adequate")],
)
def test_significance_note_scales_with_sample(n, expected):
    assert expected in significance_note(n)


def test_significance_threshold_is_thirty():
    assert MIN_MEANINGFUL_TRADES == 30
    assert "Below the" in significance_note(MIN_MEANINGFUL_TRADES - 1)
    assert "Adequate" in significance_note(MIN_MEANINGFUL_TRADES)
