#!/usr/bin/env python3
"""Download daily bars into the price cache the Gated Momentum backtest reads.

Run this on a machine where market data is actually reachable. The Claude Code
web environment blocks every market-data host at the egress proxy, so the
backtest is deliberately split: fetch once here, then `hf-bot xbacktest` runs
entirely offline against the CSVs this writes.

    pip install yfinance
    python scripts/fetch_bars.py                    # 2005 → today, default universe
    python scripts/fetch_bars.py --start 2010-01-01
    python scripts/fetch_bars.py --retry-failed     # only the ones that failed

Resumable by design: a symbol already present in the cache is skipped, so an
interrupted run is restarted by running it again. Nothing here needs the package
installed — it only needs yfinance and this repo on disk.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from hf_trading_bot.universe import LIQUID_LARGE_CAP
except Exception:  # noqa: BLE001 — run from anywhere, even without the package
    LIQUID_LARGE_CAP = []

FIELDS = ("date", "open", "high", "low", "close", "volume")


def cache_path(cache_dir: Path, symbol: str) -> Path:
    safe = symbol.upper().replace("/", "_").replace(".", "-")
    return cache_dir / f"{safe}.csv"


def fetch_one(symbol: str, start: str, end: str | None, tries: int = 3):
    """Fetch one symbol, retrying transient failures with a backoff.

    yfinance is an unofficial scraper and fails intermittently under load; a
    single miss is not evidence the symbol is unavailable, so a failure is only
    reported after the retries are exhausted.
    """
    import yfinance as yf

    last_err = None
    for attempt in range(tries):
        try:
            df = yf.Ticker(symbol).history(
                start=start, end=end, interval="1d", auto_adjust=False
            )
            if df is not None and not df.empty:
                return df
            last_err = "empty frame"
        except Exception as e:  # noqa: BLE001 — surfaced after retries
            last_err = f"{type(e).__name__}: {e}"
        if attempt < tries - 1:
            time.sleep(2 ** attempt)
    return last_err


def write_csv(path: Path, df) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for idx, row in df.iterrows():
            try:
                w.writerow([
                    idx.strftime("%Y-%m-%d"),
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]), float(row["Close"]),
                    float(row["Volume"]),
                ])
                n += 1
            except (KeyError, ValueError, TypeError):
                continue
    return n


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2005-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--cache", default=str(REPO / "data" / "bars"))
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated override for the default universe.")
    p.add_argument("--refresh", action="store_true",
                   help="Re-download symbols already cached.")
    p.add_argument("--retry-failed", action="store_true",
                   help="Only attempt symbols missing from the cache.")
    args = p.parse_args()

    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif LIQUID_LARGE_CAP:
        universe = list(LIQUID_LARGE_CAP)
    else:
        print("Could not import the default universe; pass --symbols.", file=sys.stderr)
        return 2

    cache = Path(args.cache)
    wanted = [args.benchmark] + [s for s in universe if s != args.benchmark]

    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("yfinance is not installed.  pip install yfinance", file=sys.stderr)
        return 2

    print(f"Cache:  {cache.resolve()}")
    print(f"Range:  {args.start} → {args.end or 'today'}")
    print(f"Symbols: {len(wanted)}\n")

    ok = skipped = 0
    failures: list[tuple[str, str]] = []

    for i, sym in enumerate(wanted, 1):
        path = cache_path(cache, sym)
        if path.exists() and not args.refresh:
            skipped += 1
            continue

        result = fetch_one(sym, args.start, args.end)
        if isinstance(result, str):
            failures.append((sym, result))
            print(f"  [{i:>3}/{len(wanted)}] {sym:<7} FAILED  {result[:60]}")
            continue

        n = write_csv(path, result)
        first = result.index[0].strftime("%Y-%m-%d")
        last = result.index[-1].strftime("%Y-%m-%d")
        ok += 1
        print(f"  [{i:>3}/{len(wanted)}] {sym:<7} {n:>5} bars  {first} → {last}")

    print(f"\nCached {ok} · skipped {skipped} · failed {len(failures)}")

    if failures:
        print("\nFailed symbols (re-run with --retry-failed to attempt only these):")
        for sym, err in failures:
            print(f"  {sym:<7} {err[:70]}")

    if args.benchmark not in [s for s, _ in failures] and cache_path(cache, args.benchmark).exists():
        print(f"\nNext:  hf-bot xbacktest --cache {args.cache} --start {args.start}")
    else:
        print(f"\n{args.benchmark} is missing — it is the trading calendar and the "
              f"regime input, so the backtest cannot run without it. Re-run this script.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
