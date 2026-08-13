"""SMA Crossover: entry on fast SMA crossing above slow SMA, exit on cross back below."""
from __future__ import annotations

from typing import Optional

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult


def _sma(bars: list[Bar], end: int, period: int) -> Optional[float]:
    if end < period - 1:
        return None
    window = bars[end - period + 1 : end + 1]
    return sum(b.c for b in window) / period


def sma_crossover_signal(bars: list[Bar], fast: int = 20, slow: int = 50) -> StrategyResult:
    price = bars[-1].c if bars else None
    last = len(bars) - 1
    f, s = _sma(bars, last, fast), _sma(bars, last, slow)
    pf, ps = _sma(bars, last - 1, fast), _sma(bars, last - 1, slow)
    if None in (f, s, pf, ps):
        return StrategyResult("hold", price, "not enough history")
    detail = f"sma{fast}={f:.2f} sma{slow}={s:.2f}"
    if pf <= ps and f > s:
        return StrategyResult("entry", price, detail)
    if pf >= ps and f < s:
        return StrategyResult("exit", price, detail)
    return StrategyResult("hold", price, detail)
