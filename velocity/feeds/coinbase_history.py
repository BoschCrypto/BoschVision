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


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
