"""Momentum: mom = last close / close `lookback` sessions ago - 1.

Two independent entry conditions (either can fire):
  1. CROSS  — momentum crosses negative -> positive.
  2. TREND  — momentum is above `trend_threshold`, price is above a rising
              50-day SMA, and the caller's re-entry cooldown has elapsed.
              Lets the engine join a symbol already in a strong sustained
              uptrend, which the cross-only rule could never enter.

Exit: momentum crossing positive -> negative.
"""
from __future__ import annotations

from typing import Optional

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult

TREND_ENTRY_THRESHOLD = 0.15
REENTRY_COOLDOWN_DAYS = 10


def _sma(bars: list[Bar], end: int, period: int) -> Optional[float]:
    if end < period - 1:
        return None
    window = bars[end - period + 1 : end + 1]
    return sum(b.c for b in window) / period


def momentum_signal(
    bars: list[Bar],
    lookback: int = 90,
    trend_threshold: float = TREND_ENTRY_THRESHOLD,
    trend_entry_allowed: bool = False,
    trend_sma_period: int = 50,
) -> StrategyResult:
    window = max(2, round(lookback))
    if len(bars) < window + 2:
        price = bars[-1].c if bars else None
        return StrategyResult("hold", price, "not enough history")

    last = len(bars) - 1
    mom = bars[last].c / bars[last - window].c - 1
    prev_mom = bars[last - 1].c / bars[last - 1 - window].c - 1
    price = bars[last].c

    trend_sma = _sma(bars, last, trend_sma_period)
    prev_trend_sma = _sma(bars, last - 1, trend_sma_period)
    trend_sma_rising = (
        trend_sma is not None and prev_trend_sma is not None and trend_sma > prev_trend_sma
    )

    detail = f"momentum{window}d={mom * 100:.2f}%"

    if prev_mom < 0 <= mom:
        return StrategyResult("entry", price, detail + " entry=cross", entry_kind="cross")
    if prev_mom >= 0 > mom:
        return StrategyResult("exit", price, detail + " exit")
    if (
        trend_entry_allowed
        and mom > trend_threshold
        and trend_sma is not None
        and price > trend_sma
        and trend_sma_rising
    ):
        detail += f" entry=trend (>{trend_threshold * 100:.0f}% & above rising sma{trend_sma_period})"
        return StrategyResult("entry", price, detail, entry_kind="trend")
    return StrategyResult("hold", price, detail)
