"""Replay a CSV of real ticks or bars through the engine at full speed.

Expected columns (case-insensitive, extras ignored):
    timestamp | ts | time      -> ISO8601, epoch seconds, or epoch ns
    price | close | last       -> required
    size | volume              -> optional
    bid, ask                   -> optional

Replay is where a real answer about edge comes from. Two rules, both of
which exist because breaking them is how people fool themselves:

  * The file must be out-of-sample relative to anything used to pick
    parameters. Tuning on a file and then replaying the same file measures
    memory, not skill.
  * Ticks are fed in file order with no lookahead. The engine only ever
    sees one tick at a time and cannot index forward.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import Iterator, Optional

from ..types import NS_PER_S, Tick


def _parse_ts(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return 0
    try:
        v = float(raw)
        # Heuristic on magnitude: ns, us, ms or s since epoch.
        if v > 1e17:
            return int(v)
        if v > 1e14:
            return int(v * 1_000)
        if v > 1e11:
            return int(v * 1_000_000)
        return int(v * NS_PER_S)
    except ValueError:
        pass
    s = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * NS_PER_S)


class ReplayFeed:
    def __init__(self, path: str, symbol: Optional[str] = None) -> None:
        self.path = path
        self.symbol = symbol or "REPLAY"
        self.rows_read = 0
        self.rows_skipped = 0

    def __iter__(self) -> Iterator[Tick]:
        with open(self.path, newline="") as fh:
            reader = csv.DictReader(fh)
            fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}

            def col(*names: str) -> Optional[str]:
                for n in names:
                    if n in fields:
                        return fields[n]
                return None

            c_ts = col("timestamp", "ts", "time", "date", "datetime")
            c_px = col("price", "close", "last", "c")
            c_sz = col("size", "volume", "qty", "v")
            c_bid = col("bid", "bp")
            c_ask = col("ask", "ap")
            if c_px is None:
                raise ValueError(
                    f"{self.path}: no price column found "
                    f"(looked for price/close/last/c in {reader.fieldnames})"
                )
            prev_ts = 0
            for row in reader:
                try:
                    px = float(row[c_px])
                except (TypeError, ValueError):
                    self.rows_skipped += 1
                    continue
                if px <= 0:
                    self.rows_skipped += 1
                    continue
                ts = _parse_ts(row[c_ts]) if c_ts else prev_ts + NS_PER_S
                # Out-of-order rows are a data error, not something to
                # silently sort: a sorted file hides a broken export.
                if ts < prev_ts:
                    self.rows_skipped += 1
                    continue
                prev_ts = ts
                sz = 0.0
                if c_sz:
                    try:
                        sz = float(row[c_sz] or 0.0)
                    except ValueError:
                        sz = 0.0
                bid = ask = 0.0
                if c_bid and c_ask:
                    try:
                        bid = float(row[c_bid] or 0.0)
                        ask = float(row[c_ask] or 0.0)
                    except ValueError:
                        bid = ask = 0.0
                self.rows_read += 1
                yield Tick(ts, self.symbol, px, sz, bid, ask)
