"""On-disk daily-bar cache for the cross-sectional backtester.

A universe backtest needs ~20 years of daily bars for a hundred-plus symbols.
That is far too much data to move through a chat context, and re-downloading it
on every run wastes an hour per experiment. So bars are fetched once into a
directory of per-symbol CSVs and read from there afterwards.

The cache is deliberately dumb — one CSV per symbol, ISO dates, no index files
and no binary format — so it can be inspected with `head`, edited by hand, built
by any other tool that can write a CSV, or committed if a run needs to be
reproducible later.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Optional

from hf_trading_bot.data.bars import Bar

FIELDS = ("date", "open", "high", "low", "close", "volume")


def path_for(cache_dir: Path, symbol: str) -> Path:
    # "BRK-B" and "BRK.B" both appear in index lists; normalise so the cache has
    # one file per company rather than two half-populated ones.
    safe = symbol.upper().replace("/", "_").replace(".", "-")
    return cache_dir / f"{safe}.csv"


def write(cache_dir: Path, symbol: str, bars: Iterable[Bar]) -> int:
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(bars, key=lambda b: b.t)
    with path_for(cache_dir, symbol).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for b in rows:
            w.writerow([b.t, b.o, b.h, b.l, b.c, b.v])
    return len(rows)


def read(cache_dir: Path, symbol: str) -> Optional[list[Bar]]:
    p = path_for(cache_dir, symbol)
    if not p.exists():
        return None
    out: list[Bar] = []
    with p.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(
                    Bar(
                        t=row["date"][:10],
                        o=float(row["open"]),
                        h=float(row["high"]),
                        l=float(row["low"]),
                        c=float(row["close"]),
                        v=float(row["volume"] or 0.0),
                    )
                )
            except (ValueError, KeyError, TypeError):
                # A malformed row is dropped rather than aborting the load, but
                # a file that is mostly malformed will fail the caller's
                # minimum-bars check instead of silently backtesting on scraps.
                continue
    return out or None


def load_many(
    cache_dir: Path, symbols: Iterable[str], start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[dict[str, list[Bar]], list[str]]:
    """Load symbols from cache, returning (price_data, missing)."""
    data: dict[str, list[Bar]] = {}
    missing: list[str] = []
    for s in symbols:
        bars = read(cache_dir, s)
        if not bars:
            missing.append(s)
            continue
        if start:
            bars = [b for b in bars if b.t >= start]
        if end:
            bars = [b for b in bars if b.t <= end]
        if bars:
            data[s] = bars
        else:
            missing.append(s)
    return data, missing


def coverage(cache_dir: Path) -> list[tuple[str, str, str, int]]:
    """(symbol, first_date, last_date, n_bars) for everything in the cache."""
    if not cache_dir.exists():
        return []
    out = []
    for p in sorted(cache_dir.glob("*.csv")):
        bars = read(cache_dir, p.stem)
        if bars:
            out.append((p.stem, bars[0].t, bars[-1].t, len(bars)))
    return out


# ─── Robinhood import ────────────────────────────────────────────────────────
#
# `xfetch` downloads through yfinance, which is blocked at the egress proxy in
# the hosted environment — that blocker is what stalled this backtest. The
# Robinhood MCP tool returns the same split-adjusted bars and is reachable, but
# only the agent can call it: a Python process cannot. So the fetch is split in
# two, and this is the second half:
#
#   1. the agent calls get_equity_historicals and saves each raw JSON response
#   2. `ximport` feeds those responses through here into the same CSV cache
#   3. `xbacktest` runs offline against the cache, exactly as before
#
# Nothing downstream knows or cares which half of the split produced a CSV.

def _bar_from_rh(row: dict) -> Optional[Bar]:
    """One Robinhood historicals row -> Bar; None when unusable.

    Interpolated bars are dropped. Robinhood synthesises them to fill gaps and
    they carry no new information, so admitting them would invent price history
    that never traded — and a backtest cannot tell the difference afterwards.
    """
    if row.get("interpolated") is True:
        return None
    try:
        return Bar(
            t=str(row["begins_at"])[:10],
            o=float(row["open_price"]),
            h=float(row["high_price"]),
            l=float(row["low_price"]),
            c=float(row["close_price"]),
            v=float(row.get("volume") or 0.0),
        )
    except (KeyError, ValueError, TypeError):
        return None


def import_rh(payload, cache_dir: Path) -> dict[str, int]:
    """Merge one get_equity_historicals response into the cache.

    `payload` may be the parsed dict, a JSON string, or a path to a saved
    response. Returns {symbol: total_bars_cached}. Existing bars for a symbol
    are merged rather than overwritten, so a universe can be fetched in batches
    and a long history assembled from several date ranges.
    """
    import json

    if isinstance(payload, (str, Path)):
        p = Path(str(payload))
        payload = json.loads(p.read_text() if p.exists() else str(payload))

    results = (payload or {}).get("data", {}).get("results", []) or []
    out: dict[str, int] = {}
    for entry in results:
        symbol = entry.get("symbol")
        if not symbol:
            continue
        fresh = [b for b in (_bar_from_rh(r) for r in entry.get("bars", []) or []) if b]
        if not fresh:
            out[symbol] = len(read(cache_dir, symbol) or [])
            continue
        merged = {b.t: b for b in (read(cache_dir, symbol) or [])}
        merged.update({b.t: b for b in fresh})
        out[symbol] = write(cache_dir, symbol, merged.values())
    return out
