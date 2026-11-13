"""Download perpetual funding-rate history to CSV (timestamp_s, rate).

`rate` is the fraction of position value a LONG pays a SHORT for that
interval (negative: shorts pay longs).

  deribit   hourly, from 2019, public API
  binance   every 8 hours, from January 2020, public monthly archive files
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import time
import urllib.error
import urllib.request
import zipfile

_H = {"User-Agent": "velocity-research/0.1"}


def _get(url: str, timeout: float = 40.0, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_H), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def deribit(instrument: str, start_ts: int, end_ts: int, pause_s: float = 0.15) -> list[tuple[int, float]]:
    rows: dict[int, float] = {}
    step = 20 * 86_400                                   # 480 hourly rows a call
    a = start_ts
    while a < end_ts:
        b = min(end_ts, a + step)
        url = ("https://www.deribit.com/api/v2/public/get_funding_rate_history"
               f"?instrument_name={instrument}&start_timestamp={a * 1000}&end_timestamp={b * 1000}")
        for r in json.loads(_get(url)).get("result", []):
            rows[int(r["timestamp"]) // 1000] = float(r["interest_1h"])
        a = b
        time.sleep(pause_s)
    return sorted(rows.items())


def binance(symbol: str, first=(2020, 1), last=(2026, 10), pause_s: float = 0.05) -> list[tuple[int, float]]:
    rows: dict[int, float] = {}
    y, m = first
    while (y, m) <= last:
        url = (f"https://data.binance.vision/data/futures/um/monthly/fundingRate/"
               f"{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip")
        try:
            z = zipfile.ZipFile(io.BytesIO(_get(url)))
            for line in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                rows[int(line["calc_time"]) // 1000] = float(line["last_funding_rate"])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        time.sleep(pause_s)
    return sorted(rows.items())


def save(rows: list[tuple[int, float]], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "rate"])
        w.writerows(rows)


if __name__ == "__main__":  # python -m velocity.research.funding_data OUT_DIR
    import sys
    out = sys.argv[1]
    end = calendar.timegm((2026, 11, 13, 0, 0, 0))
    for name, inst in (("BTC", "BTC-PERPETUAL"), ("ETH", "ETH-PERPETUAL")):
        rows = deribit(inst, calendar.timegm((2018, 6, 1, 0, 0, 0)), end)
        save(rows, f"{out}/{name}_deribit.csv")
        print(f"deribit {name}: {len(rows):,} hourly rows  "
              f"{time.strftime('%Y-%m-%d', time.gmtime(rows[0][0]))} to {time.strftime('%Y-%m-%d', time.gmtime(rows[-1][0]))}")
    for name, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        rows = binance(sym)
        save(rows, f"{out}/{name}_binance.csv")
        print(f"binance {name}: {len(rows):,} rows  "
              f"{time.strftime('%Y-%m-%d', time.gmtime(rows[0][0]))} to {time.strftime('%Y-%m-%d', time.gmtime(rows[-1][0]))}")
