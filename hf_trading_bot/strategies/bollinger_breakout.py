"""Bollinger Breakout: entry when price closes above the upper band (a
volatility breakout), exit when price closes back below the middle band (SMA)."""
from __future__ import annotations

import math
from typing import Optional

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult


def bollinger_bands(
    bars: list[Bar], period: int = 20, num_std: float = 2.0
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    closes = [b.c for b in bars]
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((v - mid) ** 2 for v in window) / period
    sd = math.sqrt(variance)
    return mid - num_std * sd, mid, mid + num_std * sd


def bollinger_breakout_signal(
    bars: list[Bar], period: int = 20, num_std: float = 2.0
) -> StrategyResult:
    price = bars[-1].c if bars else None
    if len(bars) < period + 1:
        return StrategyResult("hold", price, "not enough history")
    lower, mid, upper = bollinger_bands(bars, period, num_std)
    _, prev_mid, prev_upper = bollinger_bands(bars[:-1], period, num_std)
    if None in (lower, mid, upper, prev_mid, prev_upper):
        return StrategyResult("hold", price, "not enough history")
    prev_price = bars[-2].c
    detail = f"bb_mid={mid:.2f} bb_upper={upper:.2f} bb_lower={lower:.2f}"
    if prev_price <= prev_upper and price > upper:
        return StrategyResult("entry", price, detail)
    if prev_price >= prev_mid and price < mid:
        return StrategyResult("exit", price, detail)
    return StrategyResult("hold", price, detail)
