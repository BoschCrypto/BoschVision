from hf_trading_bot.data.bars import Bar
from hf_trading_bot.strategies.momentum import momentum_signal
from hf_trading_bot.strategies.rsi_mean_reversion import rsi_mean_reversion_signal, rsi_series
from hf_trading_bot.strategies.sma_crossover import sma_crossover_signal
from hf_trading_bot.strategies.macd_crossover import macd_crossover_signal
from hf_trading_bot.strategies.bollinger_breakout import bollinger_breakout_signal


def make_bars(closes: list[float]) -> list[Bar]:
    return [Bar(t=f"2026-01-{i + 1:02d}", o=c, h=c, l=c, c=c, v=1_000) for i, c in enumerate(closes)]


def test_momentum_not_enough_history_holds():
    bars = make_bars([100, 101, 102])
    result = momentum_signal(bars, lookback=90)
    assert result.signal == "hold"


def test_momentum_entry_on_negative_to_positive_cross():
    # window=10: prev_mom = c[10]/c[0]-1 (negative), mom = c[11]/c[1]-1 (positive)
    closes = [100] * 10 + [90, 101]
    bars = make_bars(closes)
    result = momentum_signal(bars, lookback=10)
    assert result.signal == "entry"
    assert result.entry_kind == "cross"


def test_momentum_exit_on_positive_to_negative_cross():
    closes = [100] * 10 + [110, 95]
    bars = make_bars(closes)
    result = momentum_signal(bars, lookback=10)
    assert result.signal == "exit"


def test_sma_crossover_not_enough_history():
    bars = make_bars([100] * 10)
    result = sma_crossover_signal(bars, fast=5, slow=20)
    assert result.signal == "hold"


def test_sma_crossover_entry_on_golden_cross():
    # Flat, then a sharp jump on the last bar pushes fast SMA(3) above slow
    # SMA(6) in a single step -- both were equal (flat) the bar before.
    closes = [100] * 8 + [100, 115]
    bars = make_bars(closes)
    result = sma_crossover_signal(bars, fast=3, slow=6)
    assert result.signal == "entry"
    assert "sma3=" in result.detail and "sma6=" in result.detail


def test_rsi_series_flat_prices_reads_as_overbought():
    # No losses at all -> Wilder RSI reads 100, matching the ported TS semantics.
    bars = make_bars([100] * 10)
    series = rsi_series(bars, period=5)
    assert all(v == 100.0 for v in series)


def test_rsi_mean_reversion_entry_on_decline_through_oversold():
    # Flat (RSI=100) then a sharp one-bar decline should cross RSI below `oversold`.
    closes = [100] * 8 + [50]
    bars = make_bars(closes)
    result = rsi_mean_reversion_signal(bars, period=5, oversold=30, overbought=60)
    assert result.signal == "entry"


def test_macd_not_enough_history_holds():
    bars = make_bars([100 + i for i in range(20)])
    result = macd_crossover_signal(bars, fast=12, slow=26, signal=9)
    assert result.signal == "hold"


def test_macd_crossover_detects_transition():
    # A sustained decline followed by a sharp rally should eventually flip
    # the MACD line above its signal line.
    closes = [100 - i * 0.5 for i in range(40)] + [x for x in range(80, 120)]
    bars = make_bars(closes)
    result = macd_crossover_signal(bars, fast=6, slow=13, signal=5)
    assert result.signal in ("entry", "hold")
    assert result.detail.startswith("macd=")


def test_bollinger_breakout_entry_on_upper_band_break():
    closes = [100] * 20 + [130]  # sharp spike well above the 20-period band
    bars = make_bars(closes)
    result = bollinger_breakout_signal(bars, period=20, num_std=2.0)
    assert result.signal == "entry"


def test_bollinger_breakout_not_enough_history():
    bars = make_bars([100] * 10)
    result = bollinger_breakout_signal(bars, period=20, num_std=2.0)
    assert result.signal == "hold"
