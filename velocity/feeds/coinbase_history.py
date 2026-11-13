"""Download 1-minute spot candles from Coinbase Exchange's public API.

No key needed. Spot is used as the price series for the perpetual-style
futures (their index is the spot price); the futures' own fees and contract
size are applied by the engine, not taken from this data.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from datetime import datetime, timezone

_URL = "https://api.exchange.coinbase.com/products/{sym}/candles?granularity=60&start={s}&end={e}"


_HEADERS = {"User-Agent": "velocity-history/0.1"}


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_rows(symbol: str, minutes: int, end_ts: int | None = None,
               pause_s: float = 0.12, timeout_s: float = 20.0) -> list[tuple[int, float, float]]:
    """The last `minutes` one-minute candles as sorted (epoch_s, close, volume).
    Raises on the first failed request: a live warm-up with holes in it is
    worse than no warm-up."""
    end_ts = int(end_ts or time.time()) // 60 * 60
    step = 300 * 60
    rows: dict[int, tuple[float, float]] = {}
    for i in range(max(1, -(-minutes // 300))):
        e = end_ts - i * step
        req = urllib.request.Request(
            _URL.format(sym=symbol, s=_iso(e - step + 60), e=_iso(e)),
            headers={"User-Agent": "velocity-history/0.1"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            for t, _lo, _hi, _op, cl, vol in json.loads(r.read()):
                rows[int(t)] = (float(cl), float(vol))
        time.sleep(pause_s)
    return [(t, rows[t][0], rows[t][1]) for t in sorted(rows)][-minutes:]


def fetch_candles(symbol: str, granularity_s: int, start_ts: int, end_ts: int,
                  pause_s: float = 0.12, timeout_s: float = 20.0, retries: int = 3) -> list[tuple[int, float, float]]:
    """Closes at any Coinbase granularity (60, 300, 900, 3600, 21600, 86400)
    between two epoch seconds, as sorted (epoch_s, close, volume). A request
    that keeps failing raises: a history with a silent hole is worse than
    none."""
    step = 300 * granularity_s
    rows: dict[int, tuple[float, float]] = {}
    e = end_ts // granularity_s * granularity_s
    while e > start_ts:
        s0 = max(start_ts, e - step + granularity_s)
        req = urllib.request.Request(
            _URL.replace("granularity=60", f"granularity={granularity_s}").format(sym=symbol, s=_iso(s0), e=_iso(e)),
            headers=_HEADERS)
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as r:
                    data = json.loads(r.read())
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(1.0 + attempt)
        for t, _lo, _hi, _op, cl, vol in data:
            rows[int(t)] = (float(cl), float(vol))
        e -= step
        time.sleep(pause_s)
    return [(t, rows[t][0], rows[t][1]) for t in sorted(rows) if start_ts <= t <= end_ts]


def last_price(symbol: str, timeout_s: float = 8.0) -> float:
    req = urllib.request.Request(
        f"https://api.exchange.coinbase.com/products/{symbol}/ticker",
        headers={"User-Agent": "velocity-history/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return float(json.loads(r.read())["price"])


def fetch_1m(symbol: str, days: float, out_path: str, end_ts: int | None = None,
             pause_s: float = 0.12, timeout_s: float = 20.0) -> dict:
    end_ts = int(end_ts or time.time()) // 60 * 60
    step = 300 * 60                      # the API returns at most 300 candles
    n_req = max(1, int(days * 1440 / 300 + 0.999))
    rows: dict[int, tuple[float, float]] = {}
    failed = 0
    for i in range(n_req):
        e = end_ts - i * step
        s = e - step + 60
        req = urllib.request.Request(
            _URL.format(sym=symbol, s=_iso(s), e=_iso(e)),
            headers={"User-Agent": "velocity-history/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data = json.loads(r.read())
        except Exception:
            failed += 1
            continue
        for t, _lo, _hi, _op, cl, vol in data:
            rows[int(t)] = (float(cl), float(vol))
        time.sleep(pause_s)
    ts = sorted(rows)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "price", "size"])
        for t in ts:
            w.writerow([t, f"{rows[t][0]:.4f}", f"{rows[t][1]:.4f}"])
    gaps = sum(1 for i in range(len(ts) - 1) if ts[i + 1] - ts[i] > 60)
    return {"bars": len(ts), "requests": n_req, "failed": failed, "gaps": gaps,
            "first": _iso(ts[0]) if ts else "", "last": _iso(ts[-1]) if ts else ""}
