"""SQLite persistence for the agency: traffic, leads, agent runs, their work,
the approval queue, security findings and the model-call ledger.

One connection shared across the web server's threads and the scheduler's
workers, serialized by a lock. At this scale (a few requests a second, a
handful of agent runs an hour) that is simpler and safer than a pool.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS pageviews (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'direct', referrer TEXT,
    utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, visitor TEXT);
CREATE INDEX IF NOT EXISTS ix_pageviews_ts ON pageviews(ts);
CREATE INDEX IF NOT EXISTS ix_pageviews_visitor ON pageviews(visitor);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, name TEXT, email TEXT,
    company TEXT, website TEXT, vertical TEXT, message TEXT,
    source TEXT, campaign TEXT, status TEXT NOT NULL DEFAULT 'new',
    score INTEGER, notes TEXT, updated_ts REAL);

CREATE TABLE IF NOT EXISTS agent_state (
    agent TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'idle', requested INTEGER NOT NULL DEFAULT 0,
    last_run_ts REAL, next_run_ts REAL, last_summary TEXT, last_error TEXT,
    rotation INTEGER NOT NULL DEFAULT 0, runs INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, agent TEXT NOT NULL, trigger TEXT,
    started_ts REAL NOT NULL, finished_ts REAL,
    status TEXT NOT NULL DEFAULT 'running', summary TEXT, error TEXT, backend TEXT);
CREATE INDEX IF NOT EXISTS ix_runs_agent ON runs(agent, id);

CREATE TABLE IF NOT EXISTS work (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, run_id INTEGER,
    kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL, lead_id INTEGER, slug TEXT UNIQUE, decided_ts REAL);
CREATE INDEX IF NOT EXISTS ix_work_status ON work(status);
CREATE INDEX IF NOT EXISTS ix_work_lead ON work(lead_id);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL,
    instruction TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', run_id INTEGER);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, run_id INTEGER,
    check_name TEXT NOT NULL, severity TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT, backend TEXT, model TEXT,
    ok INTEGER, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, error TEXT);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, key TEXT);
CREATE INDEX IF NOT EXISTS ix_events ON events(kind, key, ts);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

LEAD_STATUSES = ("prospect", "new", "contacted", "proposal", "won", "lost")


def start_of_day(now: Optional[float] = None) -> float:
    """Local midnight — budgets and daily reports reset on the owner's clock."""
    d = datetime.fromtimestamp(now if now is not None else time.time())
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:70].rstrip("-") or "post"


class Store:
    def __init__(self, path: str = "agency.db"):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- generic -----------------------------------------------------------
    def query(self, sql: str, *params: Any) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def one(self, sql: str, *params: Any) -> Optional[dict]:
        rows = self.query(sql, *params)
        return rows[0] if rows else None

    def scalar(self, sql: str, *params: Any) -> Any:
        with self._lock:
            row = self._db.execute(sql, params).fetchone()
        return row[0] if row else None

    def execute(self, sql: str, *params: Any) -> int:
        with self._lock:
            return self._db.execute(sql, params).lastrowid

    def get_kv(self, key: str, default: Any = None) -> Any:
        raw = self.scalar("SELECT value FROM kv WHERE key=?", key)
        return default if raw is None else json.loads(raw)

    def set_kv(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO kv(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     key, json.dumps(value))

    # -- traffic -----------------------------------------------------------
    def record_pageview(self, path: str, source: str, referrer: str = "",
                        utm_source: str = "", utm_medium: str = "",
                        utm_campaign: str = "", visitor: str = "",
                        ts: Optional[float] = None) -> None:
        self.execute("INSERT INTO pageviews(ts, path, source, referrer, utm_source, "
                     "utm_medium, utm_campaign, visitor) VALUES(?,?,?,?,?,?,?,?)",
                     ts or time.time(), path[:300], source[:120], referrer[:500],
                     utm_source[:120], utm_medium[:120], utm_campaign[:120], visitor)

    def first_touch(self, visitor: str, since: float) -> dict:
        """Where this visitor came from today — lets a lead be attributed to an
        ad or post without setting any cookie."""
        row = self.one("SELECT source, utm_campaign FROM pageviews WHERE visitor=? "
                       "AND ts>=? AND source!='direct' ORDER BY ts LIMIT 1", visitor, since)
        return row or {"source": "direct", "utm_campaign": ""}

    # -- events (rate limiting, auth failures) -----------------------------
    def add_event(self, kind: str, key: str = "") -> None:
        self.execute("INSERT INTO events(ts, kind, key) VALUES(?,?,?)", time.time(), kind, key)

    def count_events(self, kind: str, since: float, key: Optional[str] = None) -> int:
        if key is None:
            return self.scalar("SELECT COUNT(*) FROM events WHERE kind=? AND ts>=?", kind, since)
        return self.scalar("SELECT COUNT(*) FROM events WHERE kind=? AND key=? AND ts>=?",
                           kind, key, since)

    # -- leads -------------------------------------------------------------
    def add_lead(self, *, name: str = "", email: str = "", company: str = "",
                 website: str = "", vertical: str = "", message: str = "",
                 source: str = "direct", campaign: str = "", status: str = "new") -> int:
        now = time.time()
        return self.execute(
            "INSERT INTO leads(ts, name, email, company, website, vertical, message, "
            "source, campaign, status, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            now, name, email, company, website, vertical, message, source, campaign, status, now)

    def get_lead(self, lead_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM leads WHERE id=?", lead_id)

    def list_leads(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM leads ORDER BY id DESC LIMIT ?", limit)

    def update_lead(self, lead_id: int, **fields: Any) -> None:
        allowed = {"status", "score", "notes", "name", "email", "company", "website", "vertical"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self.execute(f"UPDATE leads SET {sets}, updated_ts=? WHERE id=?",
                     *cols.values(), time.time(), lead_id)

    def leads_awaiting(self, agent: str, statuses: Iterable[str], limit: int = 3) -> list[dict]:
        """Leads in one of `statuses` that `agent` hasn't produced work for yet."""
        statuses = tuple(statuses)
        marks = ",".join("?" * len(statuses))
        return self.query(
            f"SELECT * FROM leads l WHERE status IN ({marks}) AND NOT EXISTS "
            "(SELECT 1 FROM work w WHERE w.lead_id=l.id AND w.agent=?) ORDER BY id LIMIT ?",
            *statuses, agent, limit)

    # -- agents ------------------------------------------------------------
    def ensure_agent(self, agent: str, next_run_ts: float) -> None:
        self.execute("INSERT OR IGNORE INTO agent_state(agent, next_run_ts) VALUES(?, ?)",
                     agent, next_run_ts)

    def get_agent(self, agent: str) -> dict:
        return self.one("SELECT * FROM agent_state WHERE agent=?", agent) or {}

    def agent_states(self) -> dict[str, dict]:
        return {r["agent"]: r for r in self.query("SELECT * FROM agent_state")}

    def set_agent(self, agent: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE agent_state SET {sets} WHERE agent=?", *fields.values(), agent)

    def next_rotation(self, agent: str) -> int:
        """Return this agent's rotation counter and advance it."""
        with self._lock:
            n = self.scalar("SELECT rotation FROM agent_state WHERE agent=?", agent) or 0
            self.set_agent(agent, rotation=n + 1)
        return n

    # -- runs --------------------------------------------------------------
    def start_run(self, agent: str, trigger: str, backend: str) -> int:
        return self.execute("INSERT INTO runs(agent, trigger, started_ts, backend) VALUES(?,?,?,?)",
                            agent, trigger, time.time(), backend)

    def finish_run(self, run_id: int, status: str, summary: str = "", error: str = "") -> None:
        self.execute("UPDATE runs SET finished_ts=?, status=?, summary=?, error=? WHERE id=?",
                     time.time(), status, summary, error, run_id)

    def recent_runs(self, agent: Optional[str] = None, limit: int = 20) -> list[dict]:
        if agent:
            return self.query("SELECT * FROM runs WHERE agent=? ORDER BY id DESC LIMIT ?", agent, limit)
        return self.query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", limit)

    def runs_since(self, since: float) -> int:
        return self.scalar("SELECT COUNT(*) FROM runs WHERE started_ts>=?", since)

    def recover_interrupted(self) -> None:
        """After a crash or restart nothing is actually running any more."""
        self.execute("UPDATE runs SET status='interrupted', finished_ts=? WHERE status='running'",
                     time.time())
        self.execute("UPDATE agent_state SET status='idle' WHERE status='running'")
        self.execute("UPDATE tasks SET status='queued' WHERE status='running'")

    # -- work + approvals --------------------------------------------------
    def add_work(self, agent: str, run_id: Optional[int], kind: str, title: str,
                 body: str, status: str, lead_id: Optional[int] = None) -> int:
        return self.execute(
            "INSERT INTO work(ts, agent, run_id, kind, title, body, status, lead_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            time.time(), agent, run_id, kind, title, body, status, lead_id)

    def get_work(self, work_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM work WHERE id=?", work_id)

    def list_work(self, agent: Optional[str] = None, status: Optional[str] = None,
                  lead_id: Optional[int] = None, limit: int = 100) -> list[dict]:
        where, params = [], []
        for col, val in (("agent", agent), ("status", status), ("lead_id", lead_id)):
            if val is not None:
                where.append(f"{col}=?")
                params.append(val)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self.query(f"SELECT * FROM work {clause} ORDER BY id DESC LIMIT ?", *params, limit)

    def pending_count(self, agent: Optional[str] = None) -> int:
        if agent:
            return self.scalar("SELECT COUNT(*) FROM work WHERE status='pending' AND agent=?", agent)
        return self.scalar("SELECT COUNT(*) FROM work WHERE status='pending'")

    def recent_titles(self, agent: str, status: Optional[str] = None, limit: int = 12) -> list[str]:
        return [r["title"] for r in self.list_work(agent=agent, status=status, limit=limit)]

    def decide_work(self, work_id: int, approve: bool, body: Optional[str] = None) -> Optional[dict]:
        """Approve or reject a pending item. An approved blog post is published
        on the public site under a unique slug; everything else is marked
        approved, ready for the owner to post, launch or send."""
        with self._lock:
            item = self.get_work(work_id)
            if not item or item["status"] != "pending":
                return item
            if body is not None and body.strip():
                self.execute("UPDATE work SET body=? WHERE id=?", body.strip(), work_id)
            if not approve:
                status, slug = "rejected", None
            elif item["kind"] == "blog_post":
                status, slug = "published", slugify(item["title"])
                if self.scalar("SELECT 1 FROM work WHERE slug=?", slug):
                    slug = f"{slug}-{work_id}"
            else:
                status, slug = "approved", None
            self.execute("UPDATE work SET status=?, slug=?, decided_ts=? WHERE id=?",
                         status, slug, time.time(), work_id)
            return self.get_work(work_id)

    def published_posts(self, limit: int = 50) -> list[dict]:
        return self.query("SELECT * FROM work WHERE status='published' ORDER BY decided_ts DESC "
                          "LIMIT ?", limit)

    def post_by_slug(self, slug: str) -> Optional[dict]:
        return self.one("SELECT * FROM work WHERE status='published' AND slug=?", slug)

    # -- tasks the owner assigns from HQ -----------------------------------
    def add_task(self, agent: str, instruction: str) -> int:
        return self.execute("INSERT INTO tasks(ts, agent, instruction) VALUES(?,?,?)",
                            time.time(), agent, instruction)

    def claim_task(self, agent: str) -> Optional[dict]:
        with self._lock:
            task = self.one("SELECT * FROM tasks WHERE agent=? AND status='queued' ORDER BY id LIMIT 1",
                            agent)
            if task:
                self.execute("UPDATE tasks SET status='running' WHERE id=?", task["id"])
            return task

    def finish_task(self, task_id: int, status: str, run_id: int) -> None:
        self.execute("UPDATE tasks SET status=?, run_id=? WHERE id=?", status, run_id, task_id)

    def agents_with_queued_tasks(self) -> set[str]:
        return {r["agent"] for r in self.query("SELECT DISTINCT agent FROM tasks WHERE status='queued'")}

    def list_tasks(self, agent: Optional[str] = None, limit: int = 20) -> list[dict]:
        if agent:
            return self.query("SELECT * FROM tasks WHERE agent=? ORDER BY id DESC LIMIT ?", agent, limit)
        return self.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", limit)

    # -- security findings -------------------------------------------------
    def add_findings(self, run_id: int, findings: list[dict]) -> None:
        now = time.time()
        for f in findings:
            self.execute("INSERT INTO findings(ts, run_id, check_name, severity, ok, detail) "
                         "VALUES(?,?,?,?,?,?)", now, run_id, f["check"], f["severity"],
                         1 if f["ok"] else 0, f.get("detail", ""))

    def latest_findings(self) -> list[dict]:
        run_id = self.scalar("SELECT MAX(run_id) FROM findings")
        if run_id is None:
            return []
        return self.query("SELECT * FROM findings WHERE run_id=? ORDER BY ok, id", run_id)

    # -- model-call ledger (the daily budget) ------------------------------
    def open_llm_call(self, agent: str, backend: str, model: str) -> int:
        return self.execute("INSERT INTO llm_calls(ts, agent, backend, model) VALUES(?,?,?,?)",
                            time.time(), agent, backend, model)

    def close_llm_call(self, call_id: int, ok: bool, input_tokens: int = 0,
                       output_tokens: int = 0, cost_usd: Optional[float] = None,
                       error: str = "") -> None:
        self.execute("UPDATE llm_calls SET ok=?, input_tokens=?, output_tokens=?, cost_usd=?, "
                     "error=? WHERE id=?", 1 if ok else 0, input_tokens, output_tokens,
                     cost_usd, error, call_id)

    def llm_usage_since(self, since: float) -> dict:
        row = self.one("SELECT COUNT(*) AS calls, COALESCE(SUM(ok), 0) AS ok, "
                       "COALESCE(SUM(cost_usd), 0) AS cost_usd FROM llm_calls WHERE ts>=?", since)
        return row or {"calls": 0, "ok": 0, "cost_usd": 0.0}
