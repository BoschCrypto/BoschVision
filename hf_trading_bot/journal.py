"""Decision journal — a written record of why every position was taken.

The premise: you cannot improve as an investor without a record of what you
believed at the moment you committed capital, written *before* the outcome
was known. Memory reliably rewrites itself after the fact — winners feel
inevitable, losers feel unlucky. A contemporaneous record is the only defence
against that, and it is what turns a series of trades into actual learning.

Two rules are enforced structurally rather than by good intentions:

1. **A thesis is required before entry.** If you cannot state in three
   sentences why this is worth owning, you do not have a reason to own it.
2. **Falsification criteria are required before entry.** A thesis that cannot
   be proven wrong is a hope. Deciding what would make you exit while you are
   still calm is the entire point — nobody makes that decision well while
   watching a position bleed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,             -- BUY / SELL / HOLD / PASS
    conviction TEXT NOT NULL,           -- low / medium / high
    thesis TEXT NOT NULL,
    falsification TEXT NOT NULL,        -- what would prove me wrong
    red_team_objection TEXT,            -- strongest counter-argument, verbatim
    entry_price REAL,
    stop_price REAL,
    target_price REAL,
    position_pct REAL,                  -- % of portfolio
    benchmark_thesis TEXT,              -- why this beats just buying the index
    decided_at TEXT NOT NULL,
    -- filled in later, at review time
    outcome TEXT,                       -- right / wrong / unresolved
    exit_price REAL,
    pnl_pct REAL,
    followed_own_rules INTEGER,         -- did I exit when falsified? 1/0
    lessons TEXT,
    reviewed_at TEXT
);
"""


@dataclass
class Decision:
    symbol: str
    decision: str
    conviction: str
    thesis: str
    falsification: str
    red_team_objection: Optional[str] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    position_pct: Optional[float] = None
    benchmark_thesis: Optional[str] = None


VALID_DECISIONS = {"BUY", "SELL", "HOLD", "PASS"}
VALID_CONVICTION = {"low", "medium", "high"}


class JournalError(ValueError):
    pass


def validate(d: Decision) -> None:
    """Reject entries that skip the parts that make a journal useful."""
    if d.decision.upper() not in VALID_DECISIONS:
        raise JournalError(f"decision must be one of {sorted(VALID_DECISIONS)}")
    if d.conviction.lower() not in VALID_CONVICTION:
        raise JournalError(f"conviction must be one of {sorted(VALID_CONVICTION)}")
    if len(d.thesis.strip()) < 20:
        raise JournalError(
            "Thesis is too short to be a real thesis. State in plain language what "
            "this business does, why it is mispriced, and what closes the gap."
        )
    # PASS decisions are worth recording (they are most of them) but only a
    # committed position needs an exit plan.
    if d.decision.upper() in {"BUY", "SELL"}:
        if len(d.falsification.strip()) < 20:
            raise JournalError(
                "Falsification criteria are required before committing capital. "
                "Write the specific, observable things that would prove this wrong — "
                "decide your exit now, while you are calm."
            )


class Journal:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def record(self, d: Decision) -> int:
        validate(d)
        cur = self._conn.execute(
            """INSERT INTO decisions
               (symbol, decision, conviction, thesis, falsification, red_team_objection,
                entry_price, stop_price, target_price, position_pct, benchmark_thesis,
                decided_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d.symbol.upper(), d.decision.upper(), d.conviction.lower(),
                d.thesis.strip(), d.falsification.strip(), d.red_team_objection,
                d.entry_price, d.stop_price, d.target_price, d.position_pct,
                d.benchmark_thesis, datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def open_decisions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE outcome IS NULL AND decision IN ('BUY','SELL') "
            "ORDER BY decided_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, decision_id: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return dict(row) if row else None

    def all_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY decided_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def has_open_thesis(self, symbol: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM decisions WHERE symbol = ? AND decision = 'BUY' "
            "AND outcome IS NULL LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return row is not None

    def review(
        self,
        decision_id: int,
        outcome: str,
        exit_price: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        followed_own_rules: Optional[bool] = None,
        lessons: Optional[str] = None,
    ) -> None:
        if outcome not in {"right", "wrong", "unresolved"}:
            raise JournalError("outcome must be right / wrong / unresolved")
        self._conn.execute(
            """UPDATE decisions SET outcome=?, exit_price=?, pnl_pct=?,
               followed_own_rules=?, lessons=?, reviewed_at=? WHERE id=?""",
            (
                outcome, exit_price, pnl_pct,
                None if followed_own_rules is None else int(followed_own_rules),
                lessons, datetime.now(timezone.utc).isoformat(), decision_id,
            ),
        )
        self._conn.commit()

    def scorecard(self) -> dict[str, Any]:
        """Calibration: are you actually any good at this?

        The discipline rate matters more than the win rate. Winning while
        ignoring your own exit rules means the process is broken and the
        result was luck — that combination reliably blows up later.
        """
        rows = [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM decisions WHERE outcome IS NOT NULL AND outcome != 'unresolved'"
            ).fetchall()
        ]
        if not rows:
            return {"reviewed": 0}

        right = sum(1 for r in rows if r["outcome"] == "right")
        with_rules = [r for r in rows if r["followed_own_rules"] is not None]
        followed = sum(1 for r in with_rules if r["followed_own_rules"])
        pnls = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]

        by_conviction: dict[str, dict[str, int]] = {}
        for r in rows:
            b = by_conviction.setdefault(r["conviction"], {"n": 0, "right": 0})
            b["n"] += 1
            if r["outcome"] == "right":
                b["right"] += 1

        return {
            "reviewed": len(rows),
            "accuracy_pct": right / len(rows) * 100,
            "discipline_pct": (followed / len(with_rules) * 100) if with_rules else None,
            "avg_pnl_pct": (sum(pnls) / len(pnls)) if pnls else None,
            "by_conviction": by_conviction,
        }
