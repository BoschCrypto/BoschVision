"""The three data sources the old agents never saw.

    python -m velocity.teamwork.data dvol    OUT_DIR
    python -m velocity.teamwork.data breadth OUT_DIR [--pairs N]

VIX comes from the IBKR connector and is saved separately; there is no
public endpoint for it here.
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

_H = {"User-Agent": "velocity-research/0.1"}
START = calendar.timegm((2023, 10, 1, 0, 0, 0))     # a month of warm-up before the study window
END = calendar.timegm((2026, 11, 14, 0, 0, 0))


def _get(url: str, timeout: float = 30.0, retries: int = 5):
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_H), timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (a + 1))
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(f"gave up on {url}")


def dvol(out_dir: str) -> None:
    """Deribit's implied-volatility index, daily, for BTC and ETH."""
    os.makedirs(out_dir, exist_ok=True)
    for cur in ("BTC", "ETH"):
        rows: dict[int, float] = {}
        a = START
        while a < END:
            b = min(END, a + 200 * 86_400)
            d = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data"
                     f"?currency={cur}&start_timestamp={a * 1000}&end_timestamp={b * 1000}&resolution=43200")
            for ts, _o, _h, _l, close in (d or {}).get("result", {}).get("data", []):
                rows[int(ts) // 1000] = float(close)
            a = b
            time.sleep(0.15)
        path = os.path.join(out_dir, f"{cur}_dvol.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "dvol"])
            w.writerows(sorted(rows.items()))
        print(f"DVOL {cur}: {len(rows):,} points -> {path}", flush=True)


def breadth(out_dir: str, max_pairs: int = 400) -> None:
    """Daily closes for every live Coinbase USD pair, for a breadth measure.

    Survivor bias, stated in PLAN.md: these are the pairs listed today.
    """
    os.makedirs(out_dir, exist_ok=True)
    prods = _get("https://api.exchange.coinbase.com/products")
    usd = sorted(p["id"] for p in prods
                 if p.get("quote_currency") == "USD" and p.get("status") == "online"
                 and not p.get("trading_disabled") and not p.get("auction_mode"))[:max_pairs]
    print(f"{len(usd)} USD pairs", flush=True)
    path = os.path.join(out_dir, "breadth_daily.csv")
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            done = {r["product"] for r in csv.DictReader(fh)}
        print(f"resuming: {len(done)} pairs already saved", flush=True)
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if not done:
            w.writerow(["product", "ts", "close"])
        for n, pid in enumerate(usd, 1):
            if pid in done:
                continue
            rows: dict[int, float] = {}
            e = END
            while e > START:
                s = max(START, e - 300 * 86_400)
                d = _get("https://api.exchange.coinbase.com/products/"
                         f"{pid}/candles?granularity=86400"
                         f"&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s))}"
                         f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e))}")
                if not d:
                    break
                for t, _lo, _hi, _op, cl, _v in d:
                    rows[int(t)] = float(cl)
                e = s
                time.sleep(0.13)
            w.writerows((pid, t, c) for t, c in sorted(rows.items()))
            fh.flush()
            if n % 25 == 0:
                print(f"  {n}/{len(usd)} pairs", flush=True)
    print(f"breadth -> {path}", flush=True)


if __name__ == "__main__":
    cmd, out = sys.argv[1], sys.argv[2]
    if cmd == "dvol":
        dvol(out)
    else:
        n = int(sys.argv[sys.argv.index("--pairs") + 1]) if "--pairs" in sys.argv else 400
        breadth(out, n)
