"""One-minute OHLCV from Coinbase Exchange (public, no key), and causal bars.

    python -m velocity.shortterm.data download BTC-USD 2023-11-13 2026-11-13 OUT_DIR

Downloads are resumable: rows are appended a chunk at a time and a restart
continues from the last timestamp on disk.

All times are UTC. A candle is stamped with its START; it closes one bar
length later.
"""
from __future__ import annotations

import calendar
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

import numpy as np

_URL = "https://api.exchange.coinbase.com/products/{sym}/candles?granularity=60&start={s}&end={e}"
_HEAD = {"User-Agent": "velocity-shortterm/0.1"}
COLS = ["ts", "open", "high", "low", "close", "volume"]


def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _day(s: str) -> int:
    y, m, d = (int(x) for x in s.split("-"))
    return calendar.timegm((y, m, d, 0, 0, 0))


def download(symbol: str, start: str, end: str, out_dir: str, pause_s: float = 0.14) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{symbol}_1m_ohlcv.csv")
    t0, t1 = _day(start), _day(end)
    if os.path.exists(path) and os.path.getsize(path) > 100:
        with open(path, "rb") as fh:
            fh.seek(-200, os.SEEK_END)
            last = int(fh.read().decode().strip().splitlines()[-1].split(",")[0])
        t0 = max(t0, last + 60)
    else:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(COLS)
    step = 300 * 60
    n_req = 0
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        a = t0
        while a < t1:
            b = min(t1, a + step - 60)
            url = _URL.format(sym=symbol, s=_iso(a), e=_iso(b))
            data = None
            for attempt in range(6):
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=_HEAD), timeout=25) as r:
                        data = json.loads(r.read())
                    break
                except urllib.error.HTTPError as e:
                    time.sleep(2.0 * (attempt + 1) if e.code == 429 else 1.0 + attempt)
                except Exception:
                    time.sleep(1.0 + attempt)
            if data is None:
                raise RuntimeError(f"gave up at {_iso(a)}; rerun to resume")
            rows = sorted((int(t), op, hi, lo, cl, vol) for t, lo, hi, op, cl, vol in data if a <= int(t) <= b)
            w.writerows(rows)
            fh.flush()
            a = b + 60
            n_req += 1
            if n_req % 500 == 0:
                print(f"{symbol}: at {_iso(a)}", flush=True)
            time.sleep(pause_s)
    print(f"{symbol}: done, {n_req} requests", flush=True)
    return path


def recent(symbol: str, minutes: int) -> list[dict]:
    """The last `minutes` one-minute bars as dicts, oldest first. Missing
    minutes are filled with a flat bar at the previous close."""
    end = int(time.time()) // 60 * 60
    start = end - minutes * 60
    rows: dict[int, tuple] = {}
    a = start
    while a < end:
        b = min(end, a + 299 * 60)
        url = _URL.format(sym=symbol, s=_iso(a), e=_iso(b))
        with urllib.request.urlopen(urllib.request.Request(url, headers=_HEAD), timeout=20) as r:
            for t, lo, hi, op, cl, vol in json.loads(r.read()):
                rows[int(t)] = (op, hi, lo, cl, vol)
        a = b + 60
        if a < end:
            time.sleep(0.12)
    out, last = [], None
    for t in range(min(rows), max(rows) + 60, 60) if rows else []:
        if t in rows:
            op, hi, lo, cl, vol = rows[t]
            out.append({"ts": t, "open": op, "high": hi, "low": lo, "close": cl, "volume": vol})
            last = cl
        elif last is not None:
            out.append({"ts": t, "open": last, "high": last, "low": last, "close": last, "volume": 0.0, "gap": True})
    return out


def load(path: str) -> dict:
    """Arrays on a complete one-minute grid. A missing minute carries the
    previous close as a flat bar with zero volume and is flagged in `gap`."""
    raw = np.loadtxt(path, delimiter=",", skiprows=1)
    raw = raw[np.argsort(raw[:, 0], kind="stable")]
    raw = raw[np.concatenate(([True], np.diff(raw[:, 0]) > 0))]
    ts0, ts1 = int(raw[0, 0]), int(raw[-1, 0])
    n = (ts1 - ts0) // 60 + 1
    idx = ((raw[:, 0] - ts0) // 60).astype(np.int64)
    out = {k: np.full(n, np.nan) for k in COLS[1:]}
    for j, k in enumerate(COLS[1:], start=1):
        out[k][idx] = raw[:, j]
    gap = np.isnan(out["close"])
    # forward-fill the close, then make gap bars flat
    last = np.where(~gap, np.arange(n), 0)
    np.maximum.accumulate(last, out=last)
    close = out["close"][last]
    for k in ("open", "high", "low"):
        out[k] = np.where(gap, close, out[k])
    out["close"] = close
    out["volume"] = np.where(gap, 0.0, out["volume"])
    out["ts"] = ts0 + 60 * np.arange(n, dtype=np.int64)
    out["gap"] = gap
    return out


def resample(m1: dict, minutes: int) -> dict:
    """Bars of `minutes` length aligned to UTC, from one-minute bars. Only
    COMPLETE bars are returned; `end_i` is the index of each bar's last
    one-minute bar, so anything computed from bar k is known at end_i[k]."""
    ts = m1["ts"]
    step = minutes * 60
    first = int(np.argmax(ts % step == 0))
    n = (len(ts) - first) // minutes
    sl = slice(first, first + n * minutes)
    shp = (n, minutes)
    return {
        "ts": ts[sl].reshape(shp)[:, 0],
        "open": m1["open"][sl].reshape(shp)[:, 0],
        "high": m1["high"][sl].reshape(shp).max(axis=1),
        "low": m1["low"][sl].reshape(shp).min(axis=1),
        "close": m1["close"][sl].reshape(shp)[:, -1],
        "volume": m1["volume"][sl].reshape(shp).sum(axis=1),
        "end_i": first + minutes * np.arange(1, n + 1) - 1,
        "minutes": minutes,
    }


if __name__ == "__main__":
    if sys.argv[1] == "download":
        download(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
