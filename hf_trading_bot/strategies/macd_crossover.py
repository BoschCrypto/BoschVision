"""MACD Crossover: standard MACD(fast, slow, signal). Entry when the MACD
line crosses above its signal line, exit on cross back below."""
from __future__ import annotations

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult


def _ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def macd_series(
    bars: list[Bar], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]]:
    closes = [b.c for b in bars]
    if len(closes) < slow + signal:
        return [], [], []
    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    # fast_ema starts (slow - fast) points earlier than slow_ema; drop the lead
    # so both series align to the same bar index before differencing.
    offset = slow - fast
    fast_aligned = fast_ema[offset:]
    n = min(len(fast_aligned), len(slow_ema))
    macd_line = [fast_aligned[i] - slow_ema[i] for i in range(n)]
    signal_line = _ema_series(macd_line, signal)
    macd_aligned = macd_line[len(macd_line) - len(signal_line) :]
    histogram = [macd_aligned[i] - signal_line[i] for i in range(len(signal_line))]
    return macd_aligned, signal_line, histogram


def macd_crossover_signal(
    bars: list[Bar], fast: int = 12, slow: int = 26, signal: int = 9
) -> StrategyResult:
    price = bars[-1].c if bars else None
    macd_line, signal_line, _ = macd_series(bars, fast, slow, signal)
    if len(macd_line) < 2 or len(signal_line) < 2:
        return StrategyResult("hold", price, "not enough history")
    m, pm = macd_line[-1], macd_line[-2]
    s, ps = signal_line[-1], signal_line[-2]
    detail = f"macd={m:.3f} signal={s:.3f}"
    if pm <= ps and m > s:
        return StrategyResult("entry", price, detail)
    if pm >= ps and m < s:
        return StrategyResult("exit", price, detail)
    return StrategyResult("hold", price, detail)
