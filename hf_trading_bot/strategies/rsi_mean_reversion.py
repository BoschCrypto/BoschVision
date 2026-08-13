"""RSI Mean Reversion: entry when RSI(period) crosses below `oversold`,
exit when RSI crosses back above `overbought`."""
from __future__ import annotations

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult


def rsi_series(bars: list[Bar], period: int = 14) -> list[float]:
    """Wilder-smoothed RSI series aligned to the tail of `bars`."""
    if len(bars) < period + 1:
        return []
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = bars[i].c - bars[i - 1].c
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    gain = gains / period
    loss = losses / period
    out = [100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)]
    for i in range(period + 1, len(bars)):
        diff = bars[i].c - bars[i - 1].c
        gain = (gain * (period - 1) + max(diff, 0)) / period
        loss = (loss * (period - 1) + max(-diff, 0)) / period
        out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return out


def rsi_mean_reversion_signal(
    bars: list[Bar], period: int = 14, oversold: float = 30, overbought: float = 60
) -> StrategyResult:
    price = bars[-1].c if bars else None
    series = rsi_series(bars, period)
    if len(series) < 2:
        return StrategyResult("hold", price, "not enough history")
    rsi, prev_rsi = series[-1], series[-2]
    detail = f"rsi{period}={rsi:.1f} (in<{oversold}/out>{overbought})"
    if prev_rsi >= oversold > rsi:
        return StrategyResult("entry", price, detail)
    if prev_rsi <= overbought < rsi:
        return StrategyResult("exit", price, detail)
    return StrategyResult("hold", price, detail)
