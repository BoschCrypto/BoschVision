"""Daily OHLCV bar fetching. Backed by yfinance — no API key required."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Bar:
    t: str  # ISO date, e.g. "2026-08-01"
    o: float
    h: float
    l: float
    c: float
    v: float


def _rows_to_bars(df) -> list[Bar]:
    return [
        Bar(
            t=idx.strftime("%Y-%m-%d"),
            o=float(row["Open"]),
            h=float(row["High"]),
            l=float(row["Low"]),
            c=float(row["Close"]),
            v=float(row["Volume"]),
        )
        for idx, row in df.iterrows()
    ]


def fetch_daily_bars(symbol: str, lookback_days: int = 220) -> list[Bar]:
    """Most recent `lookback_days` daily bars for `symbol`, oldest first."""
    import yfinance as yf

    period_days = max(lookback_days + 30, 60)
    df = yf.Ticker(symbol).history(period=f"{period_days}d", interval="1d", auto_adjust=False)
    if df.empty:
        return []
    bars = _rows_to_bars(df)
    return bars[-lookback_days:] if lookback_days else bars


def fetch_daily_bars_range(symbol: str, start: str, end: Optional[str] = None) -> list[Bar]:
    """Daily bars for `symbol` across an explicit [start, end) date range."""
    import yfinance as yf

    df = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False)
    if df.empty:
        return []
    return _rows_to_bars(df)


def fetch_batch_daily_bars(symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
    return {symbol: fetch_daily_bars(symbol, lookback_days) for symbol in symbols}
