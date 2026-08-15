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

-- The committee's durable, collectively-shared memory: distilled knowledge
-- the team wants to carry forward and RECALL at the start of a future review,
-- so it builds on past work instead of starting cold. This is the honest
-- "grows and learns the more it does" — every concluded review appends an
-- episode; the next review searches these first. Lives in the repo's SQLite
-- so it persists across sessions with no external dependency; the agents
-- additionally mirror each episode into the Agently knowledge graph (a
-- cross-session/cross-tool brain) when that service is available.
CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- decision | finding | lesson | note
    symbol TEXT,                        -- ticker this concerns, if any
    title TEXT NOT NULL,
    body TEXT NOT NULL,                 -- self-contained, with absolute dates
    run_id TEXT,                        -- committee run that produced it, if any
    mirrored_to_brain INTEGER NOT NULL DEFAULT 0,  -- 1 once persisted to Agently
    created_at TEXT NOT NULL
);

-- Commands issued from the dashboard's command deck. The Python server cannot
-- run the committee agents itself (it has no LLM) — only a Claude session can.
-- So a command is QUEUED here; a real Claude session (or the opt-in
-- claude-CLI runner) executes it, which emits the committee_events that light
-- up the cortex. This table is the honest bridge between intent and execution:
-- nothing here claims a review ran until an executor actually ran it.
-- The command console: what the principal says to APEX, and what APEX says
-- back. Each row is one turn. The Python server cannot run the committee
-- itself (no LLM) — a Claude session (the opt-in claude-CLI runner, or a
-- session picking up the queue) executes APEX, which emits committee_events
-- that light the cortex and returns a reply captured here.
CREATE TABLE IF NOT EXISTS command_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- 'console' (free-form to APEX) | 'review'
    symbol TEXT,                        -- best-effort ticker, if the message named one
    message TEXT,                       -- the principal's words to APEX (raw)
    prompt TEXT NOT NULL,               -- the APEX-framed instruction for the executor
    reply TEXT,                         -- APEX's response, once it has run
    status TEXT NOT NULL,               -- pending | running | done | failed | unavailable
    detail TEXT,                        -- error text or a one-line result
    run_id TEXT,                        -- committee run this produced, once executing
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Index of what each agent has studied. The distilled lesson itself lives in
-- knowledge/<agent>/<slug>.md (git-committed, durable) and as a memory_episodes
-- row; this table just records that a curriculum topic was absorbed, so the
-- trickle knows what's next and the dashboard can show each agent's growth.
CREATE TABLE IF NOT EXISTS study_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT NOT NULL,
    topic TEXT NOT NULL,                -- curriculum topic id
    slug TEXT NOT NULL,                 -- knowledge/<agent>/<slug>.md
    sources_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(agent_key, topic)
);

-- The execution bridge: a committee decision becomes a PROPOSED order here.
-- Nothing is sent to the broker until the proposal is explicitly approved, and
-- even then only through the same guards the strategy engine enforces (kill
-- switch, position caps, paper-only). Every state transition is recorded, so
-- the path from "the committee decided" to "an order was placed" is auditable.
CREATE TABLE IF NOT EXISTS order_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER,               -- journal decision that motivated it, if any
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                -- buy | sell
    qty REAL NOT NULL,
    est_price REAL NOT NULL,
    est_notional REAL NOT NULL,
    stop_price REAL,
    take_profit REAL,
    rationale TEXT,
    broker TEXT,                       -- broker context that sized it
    status TEXT NOT NULL,              -- proposed | approved | placed | filled | rejected | failed | canceled
    broker_order_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
        self._migrate()
        self._ensure_settings_row()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a table's first release. CREATE TABLE
        IF NOT EXISTS won't alter an existing table, so add-column migrations
        live here (idempotent)."""
        cols = {r["name"] for r in
                self._conn.execute("PRAGMA table_info(command_queue)").fetchall()}
        for name in ("message", "reply"):
            if name not in cols:
                self._conn.execute(f"ALTER TABLE command_queue ADD COLUMN {name} TEXT")
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

    def latest_equity_snapshot(self) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY snapshot_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def equity_history(self, limit: int = 90) -> list[dict[str, Any]]:
        """Equity snapshots oldest-first, for charting."""
        rows = self._conn.execute(
            "SELECT equity, cash, snapshot_at FROM equity_snapshots "
            "ORDER BY snapshot_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

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

    def agent_activity_counts(self) -> dict[str, int]:
        """Per-agent tally of logged work — committee actions plus topics
        studied. Drives the growing particle density on the cortex: the more an
        agent has actually done, the denser its cloud."""
        counts: dict[str, int] = {}
        for r in self._conn.execute(
            "SELECT agent_key, COUNT(*) AS n FROM committee_events GROUP BY agent_key"
        ).fetchall():
            counts[r["agent_key"]] = counts.get(r["agent_key"], 0) + r["n"]
        for r in self._conn.execute(
            "SELECT agent_key, COUNT(*) AS n FROM study_log GROUP BY agent_key"
        ).fetchall():
            counts[r["agent_key"]] = counts.get(r["agent_key"], 0) + r["n"]
        return counts

    def committee_run_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT run_id) AS n FROM committee_events"
        ).fetchone()
        return row["n"]

    # --- collective memory -------------------------------------------------

    def record_memory_episode(
        self,
        kind: str,
        title: str,
        body: str,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        mirrored_to_brain: bool = False,
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO memory_episodes
               (kind, symbol, title, body, run_id, mirrored_to_brain, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (kind, symbol, title, body, run_id, int(mirrored_to_brain),
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def recent_memory_episodes(
        self, limit: int = 20, symbol: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if symbol:
            rows = self._conn.execute(
                "SELECT * FROM memory_episodes WHERE symbol = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memory_episodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_memory_episodes(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Simple substring recall over title/body/symbol — enough for an agent
        to pull prior conclusions on a name before starting a fresh review."""
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM memory_episodes WHERE title LIKE ? OR body LIKE ? OR symbol LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def memory_episode_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM memory_episodes").fetchone()
        return row["n"]

    # --- command queue -----------------------------------------------------

    def enqueue_command(self, kind: str, prompt: str, symbol: Optional[str] = None,
                        message: Optional[str] = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO command_queue (kind, symbol, message, prompt, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (kind, symbol, message, prompt, "pending", now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_command(
        self, command_id: int, status: str,
        detail: Optional[str] = None, run_id: Optional[str] = None,
        reply: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "UPDATE command_queue SET status = ?, detail = COALESCE(?, detail), "
            "run_id = COALESCE(?, run_id), reply = COALESCE(?, reply), updated_at = ? "
            "WHERE id = ?",
            (status, detail, run_id, reply,
             datetime.now(timezone.utc).isoformat(), command_id),
        )
        self._conn.commit()

    def pending_commands(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM command_queue WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_commands(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM command_queue ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_command(self, command_id: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM command_queue WHERE id = ?", (command_id,)
        ).fetchone()
        return dict(row) if row else None

    # --- study log ---------------------------------------------------------

    def record_study(self, agent_key: str, topic: str, slug: str,
                     sources_count: int = 0) -> int:
        """Mark a curriculum topic as absorbed by an agent. Idempotent on
        (agent, topic): re-studying updates the note count and timestamp."""
        cur = self._conn.execute(
            """INSERT INTO study_log (agent_key, topic, slug, sources_count, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(agent_key, topic) DO UPDATE SET
                   slug=excluded.slug, sources_count=excluded.sources_count,
                   created_at=excluded.created_at""",
            (agent_key, topic, slug, sources_count,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def studied_topics(self, agent_key: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT topic FROM study_log WHERE agent_key = ?", (agent_key,)
        ).fetchall()
        return {r["topic"] for r in rows}

    def study_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT agent_key, COUNT(*) AS n FROM study_log GROUP BY agent_key"
        ).fetchall()
        return {r["agent_key"]: r["n"] for r in rows}

    def recent_study(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM study_log ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def study_total(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM study_log").fetchone()["n"]

    # --- order proposals (execution bridge) --------------------------------

    def record_order_proposal(
        self, symbol: str, side: str, qty: float, est_price: float, est_notional: float,
        stop_price: Optional[float] = None, take_profit: Optional[float] = None,
        rationale: Optional[str] = None, broker: Optional[str] = None,
        decision_id: Optional[int] = None, status: str = "proposed",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO order_proposals
               (decision_id, symbol, side, qty, est_price, est_notional, stop_price,
                take_profit, rationale, broker, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, symbol.upper(), side.lower(), qty, est_price, est_notional,
             stop_price, take_profit, rationale, broker, status, now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_order_proposal(
        self, proposal_id: int, status: str,
        detail: Optional[str] = None, broker_order_id: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "UPDATE order_proposals SET status = ?, detail = COALESCE(?, detail), "
            "broker_order_id = COALESCE(?, broker_order_id), updated_at = ? WHERE id = ?",
            (status, detail, broker_order_id, datetime.now(timezone.utc).isoformat(), proposal_id),
        )
        self._conn.commit()

    def get_order_proposal(self, proposal_id: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM order_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def pending_order_proposals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM order_proposals WHERE status = 'proposed' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_order_proposals(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM order_proposals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
