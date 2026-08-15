"""Live Agent Cortex: maps the investment committee onto real, logged data.

Every agent's "firing rate" is either a genuine number computed from the
journal/sweep/watchlist tables, a disclosed proxy, or an honest "no data"
state — never a fabricated figure. See `.claude/agents/README.md` and
FINDINGS.md for the same standard applied elsewhere in this repo.

This module is the single source of truth for both delivery modes
(`hf-bot dashboard` and `hf-bot dashboard --publish`) — see cortex_render.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from .journal import Journal
from .portfolio import ContributionLog, counterfactual
from .storage import Storage

_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")


class CommandError(ValueError):
    """Raised when a dashboard command can't be parsed into a safe instruction."""


def parse_review_command(text: str) -> str:
    """Extract a ticker from free text like 'review ASTS' or 'ASTS'.

    Deliberately strict: a validated ticker is the ONLY thing that ever reaches
    an executor, so no free-form text can be smuggled into a spawned process.
    """
    if not text or not text.strip():
        raise CommandError("empty command")
    # take the longest token that looks like a ticker
    candidates = [w.strip(".,!?").upper() for w in re.split(r"[\s,]+", text.strip())]
    for w in candidates:
        if _TICKER_RE.match(w) and w not in ("REVIEW", "RUN", "THE", "A", "ON", "OF"):
            return w
    raise CommandError(
        f"could not find a ticker symbol in {text!r} — try 'review ASTS' or just 'ASTS'"
    )


def review_prompt(symbol: str) -> str:
    """The fixed instruction an executor runs. Built from a validated ticker
    only — never from raw user text."""
    return (
        f"Use the cio agent to run a full committee review on {symbol}. "
        f"Follow your activity-event protocol exactly: emit `hf-bot committee "
        f"log-event` events for the start, each delegation and finding, and the "
        f"final memo, and persist the durable conclusion with `hf-bot memory "
        f"persist` at the end, so the dashboard reflects the real run."
    )


# A hard cap so a pasted wall of text can't become a giant subprocess arg.
MAX_MESSAGE_LEN = 2000


def parse_console_message(text: str) -> str:
    """Sanitize a free-form console message to APEX. The message is passed to
    the executor as a single subprocess argument (never interpolated into a
    shell), so arbitrary text is safe; we only trim and cap length."""
    if not text or not text.strip():
        raise CommandError("empty command")
    msg = text.strip()
    if len(msg) > MAX_MESSAGE_LEN:
        raise CommandError(f"command too long (max {MAX_MESSAGE_LEN} characters)")
    return msg


def extract_symbol(text: str) -> Optional[str]:
    """Best-effort ticker for display/linking — never used to build the prompt."""
    try:
        return parse_review_command(text)
    except CommandError:
        return None


def apex_prompt(message: str) -> str:
    """Frame a principal's console message as an instruction to APEX (the cio
    agent), who orchestrates the committee and reports back in one voice.

    Written for headless execution: APEX must ACT, never ask for clarification
    (there is no one at the terminal to answer)."""
    return (
        "Act as APEX, the Chief Investment Officer who orchestrates the "
        "investment committee defined in this repository's .claude/agents/ "
        "directory. A command has arrived from your principal through the "
        "dashboard console. It is a real instruction — carry it out now.\n\n"
        f"=== COMMAND FROM YOUR PRINCIPAL ===\n{message}\n=== END COMMAND ===\n\n"
        "You are running HEADLESS: there is no one to answer follow-up "
        "questions, so do NOT ask for clarification and do NOT describe what you "
        "would do — actually do it. Make reasonable professional assumptions and "
        "act. If the command is broad or speculative (e.g. naming future "
        "winners or comeback candidates), answer it the way a CIO would — with "
        "specific candidates, base rates, and clearly-stated assumptions — never "
        "refuse for lack of certainty.\n\n"
        "As you work: choose a RUN_ID and emit committee activity with "
        "`hf-bot committee log-event` (a start event, a handoff and finding for "
        "each specialist you consult, verdicts from the risk and red-team gates, "
        "and a final memo) so the dashboard cortex reflects the run; delegate to "
        "the specialist agents as the task requires; and persist any durable "
        "conclusion with `hf-bot memory persist`.\n\n"
        "Finish with a concise report addressed to your principal, in your own "
        "voice as APEX: what you did, what the committee concluded, and your "
        "recommendation. You are the single voice back to the principal."
    )

# A run whose last event is older than this, with no memo, is treated as
# stale rather than "live" — the dashboard won't claim an agent is working
# when nothing has happened for half an hour.
ACTIVE_WINDOW = timedelta(minutes=30)

Status = Literal["live", "proxy", "no_data"]

# Fixed per-agent accent colors, keyed by agent (not codename) so a future
# codename change doesn't break color continuity.
AGENT_COLORS: dict[str, str] = {
    "cio": "#7dd3fc",
    "portfolio-manager": "#a78bfa",
    "risk-manager": "#f87171",
    "behavioral-coach": "#fb923c",
    "equity-analyst": "#4ade80",
    "quant-analyst": "#38bdf8",
    "macro-strategist": "#6b7280",
    "special-situations": "#fbbf24",
    "setup-scanner": "#22d3ee",
    "valuation-analyst": "#c084fc",
    "red-team": "#f472b6",
    "sniper": "#ef4444",
}

CODENAMES: dict[str, str] = {
    "cio": "APEX",
    "portfolio-manager": "LATTICE",
    "risk-manager": "BASTION",
    "behavioral-coach": "ECHO",
    "equity-analyst": "LEDGER",
    "quant-analyst": "CIPHER",
    "macro-strategist": "HORIZON",
    "special-situations": "EMBER",
    "setup-scanner": "RADAR",
    "valuation-analyst": "COMPASS",
    "red-team": "TALON",
    "sniper": "SNIPER",
}

ROLES: dict[str, str] = {
    "cio": "orchestrator",
    "portfolio-manager": "portfolio gate",
    "risk-manager": "risk gate",
    "behavioral-coach": "discipline gate",
    "equity-analyst": "fundamentals",
    "quant-analyst": "statistics",
    "macro-strategist": "regime",
    "special-situations": "catalysts",
    "setup-scanner": "screening",
    "valuation-analyst": "valuation",
    "red-team": "adversary",
    "sniper": "charting",
}

# Rendering layout: ring assignment for the 11-node radial layout.
RING: dict[str, str] = {
    "cio": "center",
    "portfolio-manager": "inner", "risk-manager": "inner", "behavioral-coach": "inner",
    "equity-analyst": "middle", "quant-analyst": "middle", "macro-strategist": "middle",
    "special-situations": "middle", "setup-scanner": "middle", "sniper": "middle",
    "valuation-analyst": "outer", "red-team": "outer",
}


@dataclass
class AgentReading:
    key: str
    codename: str
    role: str
    ring: str
    color: str
    metric_label: str
    metric_value: Optional[float]
    metric_display: str
    status: Status
    source: str
    note: Optional[str] = None


@dataclass
class CommitteeActivity:
    state: Literal["active", "complete", "idle"]
    run_id: Optional[str] = None
    symbol: Optional[str] = None
    active_agent: Optional[str] = None   # agent key currently working, if state=="active"
    concluded: bool = False
    started_at: Optional[str] = None
    last_at: Optional[str] = None
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CortexSnapshot:
    generated_at: str
    agents: dict[str, AgentReading] = field(default_factory=dict)
    portfolio: Optional[dict[str, Any]] = None
    open_theses: list[dict[str, Any]] = field(default_factory=list)
    latest_sweep: Optional[dict[str, Any]] = None
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    committee: Optional[dict[str, Any]] = None   # latest run activity, or None if never run
    memory: dict[str, Any] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)   # command-deck history
    knowledge: dict[str, Any] = field(default_factory=dict)        # per-agent library growth
    orders: list[dict[str, Any]] = field(default_factory=list)     # execution-bridge proposals
    account: Optional[dict[str, Any]] = None                        # balance + equity history


def _reading(
    key: str, metric_label: str, metric_value: Optional[float], metric_display: str,
    status: Status, source: str, note: Optional[str] = None,
) -> AgentReading:
    return AgentReading(
        key=key, codename=CODENAMES[key], role=ROLES[key], ring=RING[key],
        color=AGENT_COLORS[key], metric_label=metric_label, metric_value=metric_value,
        metric_display=metric_display, status=status, source=source, note=note,
    )


def _apex(all_decisions: list[dict]) -> AgentReading:
    n = len(all_decisions)
    if n == 0:
        return _reading("cio", "committee throughput", None, "—", "no_data",
                         "journal.all_decisions()", "No decisions logged yet.")
    return _reading("cio", "committee throughput", float(n), str(n), "live",
                     "journal.all_decisions()")


def _lattice(portfolio: Optional[dict]) -> AgentReading:
    if portfolio is None:
        return _reading("portfolio-manager", "SPY excess return", None, "—", "no_data",
                         "portfolio.counterfactual()",
                         "No contributions logged, no equity snapshot, or no "
                         "network for SPY bars.")
    v = portfolio["excess_pct"]
    return _reading("portfolio-manager", "SPY excess return", v, f"{v:+.1f}%", "live",
                     "portfolio.counterfactual()")


def _bastion(buy_sell: list[dict]) -> AgentReading:
    if not buy_sell:
        return _reading("risk-manager", "decisions risk-sized", None, "—", "no_data",
                         "journal decisions: stop_price & position_pct set",
                         "No BUY/SELL decisions logged yet.")
    sized = sum(1 for d in buy_sell if d["stop_price"] is not None and d["position_pct"] is not None)
    pct = sized / len(buy_sell) * 100
    return _reading("risk-manager", "decisions risk-sized", pct, f"{pct:.0f}%", "live",
                     "journal decisions: stop_price & position_pct set")


def _echo(scorecard: dict) -> AgentReading:
    if not scorecard.get("reviewed") or scorecard.get("discipline_pct") is None:
        return _reading("behavioral-coach", "rules followed", None, "—", "no_data",
                         "journal.scorecard()['discipline_pct']",
                         "No reviewed decisions with a followed/broke-rules verdict yet.")
    v = scorecard["discipline_pct"]
    return _reading("behavioral-coach", "rules followed", v, f"{v:.0f}%", "live",
                     "journal.scorecard()['discipline_pct']")


def _ledger(all_decisions: list[dict]) -> AgentReading:
    buys = [d for d in all_decisions if d["decision"] == "BUY"]
    if not buys:
        return _reading("equity-analyst", "BUY theses logged", None, "—", "no_data",
                         "journal.all_decisions()", "No BUY decisions logged yet.")
    n = len(buys)
    return _reading("equity-analyst", "BUY theses logged", float(n), str(n), "live",
                     "journal.all_decisions()")


def _cipher(latest_sweep: Optional[dict]) -> AgentReading:
    if latest_sweep is None:
        return _reading("quant-analyst", "latest sweep hit rate", None, "—", "no_data",
                         "storage.all_sweep_results()", "No sweep run yet — `hf-bot sweep`.")
    v = latest_sweep["hit_rate_pct"]
    return _reading("quant-analyst", "latest sweep hit rate", v, f"{v:.0f}%", "live",
                     "storage.all_sweep_results()")


def _horizon() -> AgentReading:
    return _reading("macro-strategist", "regime read", None, "NO SIGNAL", "no_data",
                     "none",
                     "No macro/regime table exists in this schema. A live read "
                     "would require an explicit network call this dashboard "
                     "doesn't make silently — see data/provider.py's "
                     "loud-not-silent fallback convention.")


def _sniper() -> AgentReading:
    return _reading("sniper", "chart read", None, "NO SIGNAL", "no_data", "none",
                     "Charting/timing has no logged offline metric yet — SNIPER reads "
                     "live price action on demand. Shown honestly as NO SIGNAL rather "
                     "than a fabricated number.")


def _ember(buy_sell: list[dict]) -> AgentReading:
    high = [d for d in buy_sell if d["conviction"] == "high"]
    if not high:
        return _reading("special-situations", "high-conviction calls (proxy)", None, "—",
                         "proxy", "journal decisions: conviction == 'high'",
                         "Proxy — not filtered to catalyst-driven situations; "
                         "the schema has no situation-type tag.")
    n = len(high)
    return _reading("special-situations", "high-conviction calls (proxy)", float(n), str(n),
                     "proxy", "journal decisions: conviction == 'high'",
                     "Proxy — not filtered to catalyst-driven situations; "
                     "the schema has no situation-type tag.")


def _radar(watchlist: list[dict]) -> AgentReading:
    live = [w for w in watchlist if w["live_enabled"]]
    if not live:
        return _reading("setup-scanner", "symbols in scope", None, "—", "no_data",
                         "storage.get_watchlist()", "No live-enabled watchlist symbols.")
    n = len(live)
    return _reading("setup-scanner", "symbols in scope", float(n), str(n), "live",
                     "storage.get_watchlist()")


def _compass(all_decisions: list[dict]) -> AgentReading:
    priced = [
        d for d in all_decisions
        if d["entry_price"] not in (None, 0) and d["target_price"] is not None
    ]
    if not priced:
        return _reading("valuation-analyst", "avg embedded upside", None, "—", "no_data",
                         "journal decisions: entry_price & target_price set",
                         "No decisions with both entry and target price set yet.")
    upsides = [(d["target_price"] / d["entry_price"] - 1) * 100 for d in priced]
    v = sum(upsides) / len(upsides)
    return _reading("valuation-analyst", "avg embedded upside", v, f"{v:+.1f}%", "live",
                     "journal decisions: entry_price & target_price set")


def _talon(buy_sell: list[dict]) -> AgentReading:
    if not buy_sell:
        return _reading("red-team", "theses challenged", None, "—", "no_data",
                         "journal decisions: red_team_objection set",
                         "No BUY/SELL decisions logged yet.")
    challenged = sum(1 for d in buy_sell if d["red_team_objection"])
    pct = challenged / len(buy_sell) * 100
    return _reading("red-team", "theses challenged", pct, f"{pct:.0f}%", "live",
                     "journal decisions: red_team_objection set")


def _portfolio_panel(storage: Storage, clog: ContributionLog) -> Optional[dict[str, Any]]:
    contributions = clog.all()
    if not contributions:
        return None
    row = storage._conn.execute(
        "SELECT equity FROM equity_snapshots ORDER BY snapshot_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    from .data.provider import get_provider

    try:
        provider = get_provider()
        bars = provider.daily_bars_range("SPY", start=clog.first_date())
        c = counterfactual(contributions, bars, actual_value=row["equity"])
    except Exception:
        # Network unreachable, no SPY bars, or bad contribution dates. The CLI
        # prints a loud one-line reason before rendering; the panel degrades to
        # an honest empty state rather than crashing the whole dashboard.
        return None
    return {
        "gap": c.gap,
        "excess_pct": c.excess_pct,
        "as_of": c.as_of,
        "actual_return_pct": c.actual_return_pct,
        "benchmark_return_pct": c.benchmark_return_pct,
        "total_contributed": c.total_contributed,
        "actual_value": c.actual_value,
        "benchmark_value": c.benchmark_value,
    }


def _committee_activity(storage: Storage) -> Optional[dict[str, Any]]:
    run_id = storage.latest_committee_run_id()
    if run_id is None:
        return None
    events = storage.committee_run_events(run_id)
    if not events:
        return None

    concluded = any(e["event_type"] == "memo" for e in events)
    last = events[-1]
    started_at = events[0]["logged_at"]
    last_at = last["logged_at"]

    # Recency: only claim "active" if something happened recently and no memo
    # has been issued. A stale unfinished run is shown but not animated as live.
    recent = False
    try:
        recent = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)) <= ACTIVE_WINDOW
    except ValueError:
        recent = False

    if concluded:
        state = "complete"
        active_agent = None
    elif recent:
        state = "active"
        # After a handoff, the target agent is the one now working.
        active_agent = last["to_agent"] if last["event_type"] == "handoff" and last["to_agent"] else last["agent_key"]
    else:
        state = "idle"
        active_agent = None

    return {
        "state": state,
        "run_id": run_id,
        "symbol": last["symbol"],
        "active_agent": active_agent,
        "concluded": concluded,
        "started_at": started_at,
        "last_at": last_at,
        "events": events,
    }


def _memory(storage: Storage, journal: Journal, all_decisions: list[dict], scorecard: dict) -> dict[str, Any]:
    """The committee's collective, growing record. Not a model that gets
    'smarter' — an accumulating body of decisions and outcomes that makes
    calibration measurable. It grows every time the team does real work."""
    lessons = storage._conn.execute(
        "SELECT COUNT(*) AS n FROM decisions WHERE lessons IS NOT NULL AND lessons != ''"
    ).fetchone()["n"]
    episodes = storage.recent_memory_episodes(limit=6)
    mirrored = storage._conn.execute(
        "SELECT COUNT(*) AS n FROM memory_episodes WHERE mirrored_to_brain = 1"
    ).fetchone()["n"]
    return {
        "decisions_logged": len(all_decisions),
        "reviewed": scorecard.get("reviewed", 0),
        "committee_runs": storage.committee_run_count(),
        "lessons_captured": lessons,
        "discipline_pct": scorecard.get("discipline_pct"),
        "accuracy_pct": scorecard.get("accuracy_pct"),
        "episodes_count": storage.memory_episode_count(),
        "episodes_mirrored": mirrored,
        "recent_episodes": [
            {"kind": e["kind"], "symbol": e["symbol"], "title": e["title"],
             "mirrored": bool(e["mirrored_to_brain"])}
            for e in episodes
        ],
    }


def _knowledge(storage: Storage) -> dict[str, Any]:
    """Per-agent curriculum coverage — the team's accumulated, recallable
    expertise. Grows as agents study; the dashboard renders it honestly as a
    library, not as a retrained model."""
    from .curriculum import coverage, load_curriculum

    curric = load_curriculum()
    per_agent = {}
    absorbed_total = 0
    topic_total = 0
    for agent_key in curric:
        studied = storage.studied_topics(agent_key)
        a, t = coverage(agent_key, studied)
        per_agent[agent_key] = {"absorbed": a, "total": t}
        absorbed_total += a
        topic_total += t
    return {
        "per_agent": per_agent,
        "absorbed_total": absorbed_total,
        "topic_total": topic_total,
        "recent": [
            {"agent": r["agent_key"], "topic": r["topic"], "slug": r["slug"]}
            for r in storage.recent_study(limit=6)
        ],
    }


def build_snapshot(storage: Storage) -> CortexSnapshot:
    journal = Journal(storage._conn)
    clog = ContributionLog(storage._conn)

    all_decisions = journal.all_decisions(limit=10_000)
    buy_sell = [d for d in all_decisions if d["decision"] in ("BUY", "SELL")]
    scorecard = journal.scorecard()
    sweep_rows = storage.all_sweep_results(limit=1)
    latest_sweep = sweep_rows[0] if sweep_rows else None
    watchlist = storage.get_watchlist()
    portfolio = _portfolio_panel(storage, clog)

    agents = {
        "cio": _apex(all_decisions),
        "portfolio-manager": _lattice(portfolio),
        "risk-manager": _bastion(buy_sell),
        "behavioral-coach": _echo(scorecard),
        "equity-analyst": _ledger(all_decisions),
        "quant-analyst": _cipher(latest_sweep),
        "macro-strategist": _horizon(),
        "special-situations": _ember(buy_sell),
        "setup-scanner": _radar(watchlist),
        "valuation-analyst": _compass(all_decisions),
        "red-team": _talon(buy_sell),
        "sniper": _sniper(),
    }

    return CortexSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        agents=agents,
        portfolio=portfolio,
        open_theses=journal.open_decisions(),
        latest_sweep=latest_sweep,
        watchlist=watchlist,
        committee=_committee_activity(storage),
        memory=_memory(storage, journal, all_decisions, scorecard),
        commands=[
            {"id": c["id"], "symbol": c["symbol"], "status": c["status"],
             "detail": c["detail"], "created_at": c["created_at"],
             "message": c.get("message"), "reply": c.get("reply")}
            for c in storage.recent_commands(limit=8)
        ],
        knowledge=_knowledge(storage),
        orders=[
            {"id": o["id"], "symbol": o["symbol"], "side": o["side"], "qty": o["qty"],
             "est_notional": o["est_notional"], "status": o["status"],
             "stop_price": o["stop_price"], "detail": o["detail"]}
            for o in storage.recent_order_proposals(limit=8)
        ],
        account=_account(storage),
    )


def _account(storage: Storage) -> Optional[dict[str, Any]]:
    latest = storage.latest_equity_snapshot()
    if latest is None:
        return None
    hist = storage.equity_history(limit=90)
    equities = [h["equity"] for h in hist]
    first = equities[0] if equities else latest["equity"]
    return {
        "equity": latest["equity"],
        "cash": latest["cash"],
        "as_of": latest["snapshot_at"],
        "change_pct": ((latest["equity"] / first - 1) * 100) if first else 0.0,
        "history": [{"t": h["snapshot_at"][:10], "equity": h["equity"]} for h in hist],
    }
