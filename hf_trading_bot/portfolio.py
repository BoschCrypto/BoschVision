"""Contribution tracking and the benchmark counterfactual.

Answers the only question that ultimately matters for a retail account:
given the money actually deposited, on the dates it was actually deposited,
is this portfolio ahead of or behind simply buying an index fund with the
same cash flows?

Everything that touches the market lives behind the data provider, so the
counterfactual math here is a pure function and is tested offline.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .data.bars import Bar

SCHEMA = """
CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contributed_on TEXT NOT NULL,       -- ISO date, e.g. "2026-08-01"
    amount REAL NOT NULL,               -- positive = deposit, negative = withdrawal
    note TEXT,
    recorded_at TEXT NOT NULL
);
"""


class PortfolioError(ValueError):
    """Raised when contributions or price data cannot support a comparison."""


@dataclass
class Contribution:
    contributed_on: str
    amount: float
    note: Optional[str] = None


@dataclass
class Comparison:
    """Actual portfolio vs. the same cash flows put into the benchmark."""
    benchmark: str
    total_contributed: float
    actual_value: float
    benchmark_value: float
    benchmark_shares: float
    as_of: str

    @property
    def gap(self) -> float:
        """Dollars ahead (+) or behind (-) the benchmark."""
        return self.actual_value - self.benchmark_value

    @property
    def actual_return_pct(self) -> float:
        if self.total_contributed <= 0:
            return 0.0
        return (self.actual_value / self.total_contributed - 1) * 100

    @property
    def benchmark_return_pct(self) -> float:
        if self.total_contributed <= 0:
            return 0.0
        return (self.benchmark_value / self.total_contributed - 1) * 100

    @property
    def excess_pct(self) -> float:
        return self.actual_return_pct - self.benchmark_return_pct


def _price_on_or_after(bars: list[Bar], date: str) -> float:
    """Close on `date`, or the next trading day if it was a weekend/holiday.

    Contributions land on calendar dates; markets do not trade every calendar
    date. Buying at the next available close is what would actually happen.
    """
    for b in bars:
        if b.t >= date:
            return b.c
    raise PortfolioError(
        f"no benchmark price on or after {date} — the contribution date is "
        f"later than the last available bar ({bars[-1].t})"
    )


def counterfactual(
    contributions: list[Contribution],
    bars: list[Bar],
    actual_value: float,
    benchmark: str = "SPY",
) -> Comparison:
    """What the same deposits would be worth in `benchmark` today.

    Each contribution buys fractional shares at the benchmark close on (or
    after) its date; withdrawals sell shares at that close. The resulting
    share count is marked at the most recent close.
    """
    if not bars:
        raise PortfolioError(f"no price history for benchmark {benchmark}")
    if not contributions:
        raise PortfolioError(
            "no contributions recorded — log deposits with "
            "`hf-bot portfolio contribute` before comparing"
        )

    shares = 0.0
    for c in contributions:
        shares += c.amount / _price_on_or_after(bars, c.contributed_on)

    latest = bars[-1]
    return Comparison(
        benchmark=benchmark,
        total_contributed=sum(c.amount for c in contributions),
        actual_value=actual_value,
        benchmark_value=shares * latest.c,
        benchmark_shares=shares,
        as_of=latest.t,
    )


class ContributionLog:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def add(self, c: Contribution) -> int:
        try:
            datetime.strptime(c.contributed_on, "%Y-%m-%d")
        except ValueError:
            raise PortfolioError(
                f"contributed_on must be YYYY-MM-DD, got {c.contributed_on!r}"
            )
        if c.amount == 0:
            raise PortfolioError("amount must be non-zero")
        cur = self._conn.execute(
            "INSERT INTO contributions (contributed_on, amount, note, recorded_at) "
            "VALUES (?,?,?,?)",
            (c.contributed_on, c.amount, c.note,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def all(self) -> list[Contribution]:
        rows = self._conn.execute(
            "SELECT contributed_on, amount, note FROM contributions "
            "ORDER BY contributed_on ASC"
        ).fetchall()
        return [Contribution(r[0], r[1], r[2]) for r in rows]

    def rows(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM contributions ORDER BY contributed_on ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def first_date(self) -> Optional[str]:
        row = self._conn.execute(
            "SELECT MIN(contributed_on) FROM contributions"
        ).fetchone()
        return row[0] if row and row[0] else None
