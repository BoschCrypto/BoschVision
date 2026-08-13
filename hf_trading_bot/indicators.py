"""Shared technical indicators used by both the strategy engine and risk sizing."""
from __future__ import annotations

from typing import Optional

from hf_trading_bot.data.bars import Bar


def atr(bars: list[Bar], period: int = 14) -> Optional[float]:
    """Wilder Average True Range over `period` sessions (None when not enough bars)."""
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        b = bars[i]
        prev_close = bars[i - 1].c
        trs.append(max(b.h - b.l, abs(b.h - prev_close), abs(b.l - prev_close)))
    value = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        value = (value * (period - 1) + trs[i]) / period
    return value
