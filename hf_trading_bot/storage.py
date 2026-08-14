"""Local SQLite persistence: settings, watchlist, trades, equity snapshots,
signal log. Single-user, single-file — no external database required."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kill_switch_active INTEGER NOT NULL DEFAULT 1,
    exits_allowed_when_paused INTEGER NOT NULL DEFAULT 1,
    max_daily_loss_pct REAL NOT NULL DEFAULT 5,
    max_drawdown_pct REAL NOT NULL DEFAULT 15,
    max_weekly_loss_pct REAL NOT NULL DEFAULT 6,
    max_consecutive_losses INTEGER NOT NULL DEFAULT 3,
    max_position_pct REAL NOT NULL DEFAULT 20,
    max_portfolio_exposure_pct REAL NOT NULL DEFAULT 80,
    max_open_positions INTEGER NOT NULL DEFAULT 5,
    risk_per_trade_pct REAL NOT NULL DEFAULT 1,
    atr_stop_multiple REAL NOT NULL DEFAULT 2,
    stop_loss_pct REAL NOT NULL DEFAULT 8,
    take_profit_r REAL NOT NULL DEFAULT 3,
    trailing_stop_pct REAL NOT NULL DEFAULT 12,
    trail_activate_r REAL NOT NULL DEFAULT 1.5,
    equity_high_water_mark REAL NOT NULL DEFAULT 0,
    week_start_on TEXT,
    week_start_equity REAL,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    weekly_loss_tripped_week TEXT,
    consecutive_loss_tripped_week TEXT,
    max_day_trades INTEGER NOT NULL DEFAULT 3,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_symbols (
    symbol TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL,
    live_enabled INTEGER NOT NULL DEFAULT 1,
    params TEXT NOT NULL DEFAULT '{}',
    rank INTEGER NOT NULL DEFAULT 9999
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL,
    exit_price REAL,
    pnl_usd REAL,
    pnl_pct REAL,
    strategy_key TEXT NOT NULL,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    stop_price REAL,
    take_profit_price REAL,
    exit_reason TEXT,
    opened_at TEXT,
    traded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    snapshot_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    signal TEXT NOT NULL,
    detail TEXT NOT NULL,
    would_have_traded INTEGER NOT NULL,
    logged_at TEXT NOT NULL
);

-- One row per strategy per sweep run. Kept so a tested-and-rejected strategy
-- stays rejected: without a record, an uncomfortable result quietly decays
-- into "it was roughly break-even" and gets paid for twice.
CREATE TABLE IF NOT EXISTS sweep_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,               -- groups all strategies from one sweep
    strategy_key TEXT NOT NULL,
    symbols_tested INTEGER NOT NULL,
    wins INTEGER NOT NULL,              -- symbols where the strategy beat buy&hold
    hit_rate_pct REAL NOT NULL,
    median_excess_pts REAL NOT NULL,
    total_trades INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT,
    run_at TEXT NOT NULL
);

-- The committee's working record: one row per agent action during a review.
-- This is what makes the dashboard's "agents interacting" real rather than
-- decorative — every handoff pulse and active-node glow corresponds to an
-- actual logged event emitted by an agent as it worked. No event, no motion.
CREATE TABLE IF NOT EXISTS committee_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,               -- groups all events from one review
    seq INTEGER NOT NULL,               -- order within the run
    symbol TEXT,                        -- what's under review (from the run's first event)
    agent_key TEXT NOT NULL,            -- who acted (e.g. "cio", "red-team")
    event_type TEXT NOT NULL,           -- start | handoff | finding | verdict | memo
    to_agent TEXT,                      -- handoff target, when event_type = handoff
    summary TEXT NOT NULL,              -- one line: what happened
    logged_at TEXT NOT NULL
);
"""

DEFAULT_WATCHLIST = [
    ("NVDA", "momentum_90d"),
    ("QQQ", "momentum_90d"),
    ("VGT", "rsi_mean_reversion"),
    ("NKE", "sma_crossover"),
]


class Storage:
    def __init__(self, path: str = "trading_bot.db"):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._ensure_settings_row()
        self._conn.commit()

    def _ensure_settings_row(self) -> None:
        cur = self._conn.execute("SELECT id FROM settings WHERE id = 1")
        if cur.fetchone() is None:
            self._conn.execute(
                "INSERT INTO settings (id, updated_at) VALUES (1, ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def get_settings(self) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return dict(row)

    def update_settings(self, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE settings SET {cols} WHERE id = 1", list(fields.values()))
        self._conn.commit()

    def set_kill_switch(self, active: bool) -> None:
        self.update_settings(kill_switch_active=int(active))

    def get_watchlist(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM watchlist_symbols ORDER BY rank, symbol").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"] or "{}")
            d["live_enabled"] = bool(d["live_enabled"])
            out.append(d)
        return out

    def upsert_watchlist_symbol(
        self,
        symbol: str,
        strategy_key: str,
        live_enabled: bool = True,
        params: Optional[dict] = None,
        rank: int = 9999,
    ) -> None:
        self._conn.execute(
            """INSERT INTO watchlist_symbols (symbol, strategy_key, live_enabled, params, rank)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET strategy_key=excluded.strategy_key,
                 live_enabled=excluded.live_enabled, params=excluded.params, rank=excluded.rank""",
            (symbol, strategy_key, int(live_enabled), json.dumps(params or {}), rank),
        )
        self._conn.commit()

    def remove_watchlist_symbol(self, symbol: str) -> None:
        self._conn.execute("DELETE FROM watchlist_symbols WHERE symbol = ?", (symbol,))
        self._conn.commit()

    def seed_default_watchlist(self) -> None:
        if self.get_watchlist():
            return
        for i, (symbol, strategy) in enumerate(DEFAULT_WATCHLIST):
            self.upsert_watchlist_symbol(symbol, strategy, rank=i)

    def record_trade(self, **fields: Any) -> int:
        fields.setdefault("traded_at", datetime.now(timezone.utc).isoformat())
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = self._conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders})", list(fields.values())
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_open_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        """Most recent buy trades per symbol — used to recover the stop price
        recorded at entry for symbols still held."""
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE side = 'buy' ORDER BY traded_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def day_trades_in_window(self, start_date: str, end_date: str) -> int:
        """Count day trades — a symbol bought and sold on the same date —
        with traded_at date in [start_date, end_date] inclusive (YYYY-MM-DD).
        Used to enforce the Pattern Day Trader (PDT) limit of 3 day trades
        per rolling 5 business days on accounts under $25k."""
        rows = self._conn.execute(
            """SELECT symbol, date(traded_at) AS d,
                      SUM(side = 'buy') AS buys, SUM(side = 'sell') AS sells
               FROM trades
               WHERE date(traded_at) BETWEEN ? AND ?
               GROUP BY symbol, d
               HAVING buys > 0 AND sells > 0""",
            (start_date, end_date),
        ).fetchall()
        return len(rows)

    def record_equity_snapshot(self, equity: float, cash: float) -> None:
        self._conn.execute(
            "INSERT INTO equity_snapshots (equity, cash, snapshot_at) VALUES (?, ?, ?)",
            (equity, cash, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def log_signal(
        self, symbol: str, strategy_key: str, signal: str, detail: str, would_have_traded: bool
    ) -> None:
        self._conn.execute(
            """INSERT INTO signal_log (symbol, strategy_key, signal, detail, would_have_traded, logged_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, strategy_key, signal, detail, int(would_have_traded), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def record_sweep_result(
        self,
        run_id: str,
        strategy_key: str,
        symbols_tested: int,
        wins: int,
        hit_rate_pct: float,
        median_excess_pts: float,
        total_trades: int,
        window_start: str,
        window_end: Optional[str] = None,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO sweep_results
               (run_id, strategy_key, symbols_tested, wins, hit_rate_pct,
                median_excess_pts, total_trades, window_start, window_end, run_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, strategy_key, symbols_tested, wins, hit_rate_pct,
             median_excess_pts, total_trades, window_start, window_end,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def latest_sweep_result(self, strategy_key: str) -> Optional[dict[str, Any]]:
        """Most recent recorded sweep for a strategy, or None if never tested."""
        row = self._conn.execute(
            "SELECT * FROM sweep_results WHERE strategy_key = ? "
            "ORDER BY run_at DESC LIMIT 1",
            (strategy_key,),
        ).fetchone()
        return dict(row) if row else None

    def all_sweep_results(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM sweep_results ORDER BY run_at DESC, strategy_key ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- committee activity ------------------------------------------------

    def record_committee_event(
        self,
        run_id: str,
        agent_key: str,
        event_type: str,
        summary: str,
        symbol: Optional[str] = None,
        to_agent: Optional[str] = None,
    ) -> int:
        """Append one agent action to a committee run. `seq` is assigned as the
        next integer within the run, so events order deterministically even if
        two land in the same millisecond."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM committee_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seq = row["n"]
        # A run's symbol is set by its first event; later events inherit it if
        # the caller didn't repeat it.
        if symbol is None:
            prior = self._conn.execute(
                "SELECT symbol FROM committee_events WHERE run_id = ? AND symbol IS NOT NULL "
                "ORDER BY seq LIMIT 1",
                (run_id,),
            ).fetchone()
            symbol = prior["symbol"] if prior else None
        cur = self._conn.execute(
            """INSERT INTO committee_events
               (run_id, seq, symbol, agent_key, event_type, to_agent, summary, logged_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, seq, symbol, agent_key, event_type, to_agent, summary,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def committee_run_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM committee_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_committee_run_id(self) -> Optional[str]:
        row = self._conn.execute(
            "SELECT run_id FROM committee_events ORDER BY logged_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def committee_run_ids(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent runs, newest first: run_id, symbol, event count, last activity."""
        rows = self._conn.execute(
            """SELECT run_id,
                      MAX(symbol) AS symbol,
                      COUNT(*) AS events,
                      MAX(logged_at) AS last_at,
                      MAX(CASE WHEN event_type = 'memo' THEN 1 ELSE 0 END) AS concluded
               FROM committee_events
               GROUP BY run_id
               ORDER BY last_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def committee_run_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT run_id) AS n FROM committee_events"
        ).fetchone()
        return row["n"]

    def close(self) -> None:
        self._conn.close()
