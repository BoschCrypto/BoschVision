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
