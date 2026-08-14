"""Daily OHLCV bars from Alpaca's official Market Data API.

Ported from the Apex Trading Hub dashboard's `src/lib/alpaca.server.ts`
(`getDailyBars`): 50-symbol chunking, `page_token` pagination, split
adjustment, IEX feed.

Feed note: the free Alpaca tier serves the **IEX** feed, not the full SIP
consolidated tape. IEX is a subset of total market volume, so daily bars can
differ slightly from other charting sources. That is fine for daily-bar swing
strategies but worth knowing before comparing numbers against TradingView et
al. Override with ALPACA_DATA_FEED=sip if the account has a SIP subscription.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from .bars import Bar

DATA_BASE = "https://data.alpaca.markets"
CHUNK = 50
_PAGE_GUARD = 40  # hard cap on pagination loops; Alpaca pages are 10k bars
_TIMEOUT = 30

# Trading days are ~69% of calendar days. Overshoot the calendar window so a
# request for N bars actually comes back with N, then trim to size.
_CALENDAR_SLACK = 1.6


class AlpacaCredentialsMissing(RuntimeError):
    """Raised when the Alpaca API key/secret aren't in the environment."""


def _headers() -> dict[str, str]:
    key_id = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key_id or not secret:
        raise AlpacaCredentialsMissing(
            "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in your environment "
            "or .env file (see .env.example)."
        )
    return {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret,
        "content-type": "application/json",
    }


def _feed() -> str:
    return os.environ.get("ALPACA_DATA_FEED", "iex")


def _row_to_bar(row: dict) -> Bar:
    # Alpaca timestamps are RFC3339 ("2026-08-01T04:00:00Z"); the first 10
    # chars give the same "YYYY-MM-DD" the yfinance provider produces, so
    # Bar.t stays directly comparable across providers.
    return Bar(
        t=str(row["t"])[:10],
        o=float(row["o"]),
        h=float(row["h"]),
        l=float(row["l"]),
        c=float(row["c"]),
        v=float(row.get("v") or 0),
    )


def _start_for_lookback(lookback_days: int) -> str:
    calendar_days = int(lookback_days * _CALENDAR_SLACK) + 10
    return (datetime.now(timezone.utc).date() - timedelta(days=calendar_days)).isoformat()


def _fetch_bars(
    symbols: list[str], start: str, end: Optional[str] = None
) -> dict[str, list[Bar]]:
    """Raw paginated fetch across all `symbols` for the [start, end] window."""
    if not symbols:
        return {}
    headers = _headers()
    feed = _feed()
    out: dict[str, list[Bar]] = {}

    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        page_token: Optional[str] = None
        for _ in range(_PAGE_GUARD):
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start,
                "limit": "10000",
                "adjustment": "split",
                "feed": feed,
            }
            if end:
                params["end"] = end
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(
                f"{DATA_BASE}/v2/stocks/bars",
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Alpaca data API {resp.status_code}: {resp.text[:300]}"
                )
            payload = resp.json()
            for symbol, rows in (payload.get("bars") or {}).items():
                out.setdefault(symbol, []).extend(_row_to_bar(r) for r in rows)

            page_token = payload.get("next_page_token")
            if not page_token:
                break

    # Alpaca returns bars oldest-first per page; guarantee ordering anyway
    # since chunk/page interleaving isn't contractually sorted.
    for series in out.values():
        series.sort(key=lambda b: b.t)
    return out


def fetch_daily_bars(symbol: str, lookback_days: int = 220) -> list[Bar]:
    """Most recent `lookback_days` daily bars for `symbol`, oldest first."""
    bars = _fetch_bars([symbol], _start_for_lookback(lookback_days)).get(symbol, [])
    return bars[-lookback_days:] if lookback_days else bars


def fetch_daily_bars_range(
    symbol: str, start: str, end: Optional[str] = None
) -> list[Bar]:
    """Daily bars for `symbol` across an explicit [start, end] date range."""
    return _fetch_bars([symbol], start, end).get(symbol, [])


def fetch_batch_daily_bars(
    symbols: list[str], lookback_days: int = 220
) -> dict[str, list[Bar]]:
    raw = _fetch_bars(symbols, _start_for_lookback(lookback_days))
    if not lookback_days:
        return raw
    return {symbol: series[-lookback_days:] for symbol, series in raw.items()}
