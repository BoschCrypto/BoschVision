"""Cortex data layer + a light render smoke test. All offline, no network."""
import sqlite3

import pytest

from hf_trading_bot.cortex import (
    CommandError,
    build_snapshot,
    parse_review_command,
    review_prompt,
)
from hf_trading_bot.cortex_render import render_html
from hf_trading_bot.journal import Decision, Journal
from hf_trading_bot.portfolio import Contribution, ContributionLog
from hf_trading_bot.storage import Storage

ALL_KEYS = [
    "cio", "portfolio-manager", "risk-manager", "behavioral-coach", "equity-analyst",
    "quant-analyst", "macro-strategist", "special-situations", "setup-scanner",
    "valuation-analyst", "red-team",
]


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "cortex.db"))
    yield s
    s.close()


def _buy(j, symbol, **kw):
    base = dict(
        symbol=symbol, decision="BUY", conviction="medium",
        thesis="x" * 40, falsification="y" * 40,
    )
    base.update(kw)
    return j.record(Decision(**base))


# --- empty DB ---------------------------------------------------------------

def test_empty_db_has_all_eleven_agents(storage):
    snap = build_snapshot(storage)
    assert set(snap.agents) == set(ALL_KEYS)


def test_empty_db_agents_are_no_data_or_proxy(storage):
    snap = build_snapshot(storage)
    # EMBER is always "proxy" (even at zero); everything else is "no_data" empty.
    for key, r in snap.agents.items():
        if key == "special-situations":
            assert r.status == "proxy"
        else:
            assert r.status == "no_data"


def test_empty_db_panels_are_empty(storage):
    snap = build_snapshot(storage)
    assert snap.portfolio is None
    assert snap.open_theses == []
    assert snap.latest_sweep is None
    # A raw Storage has no seeded watchlist (that happens in _load_storage).
    assert snap.watchlist == []


def test_horizon_is_always_no_data_even_when_populated(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", conviction="high", stop_price=90, position_pct=5)
    snap = build_snapshot(storage)
    assert snap.agents["macro-strategist"].status == "no_data"
    assert snap.agents["macro-strategist"].metric_display == "NO SIGNAL"


# --- individual metrics -----------------------------------------------------

def test_apex_counts_all_decisions_regardless_of_type(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA")
    j.record(Decision(symbol="INTC", decision="PASS", conviction="low",
                      thesis="x" * 40, falsification="y" * 40))
    snap = build_snapshot(storage)
    assert snap.agents["cio"].metric_value == 2.0
    assert snap.agents["cio"].status == "live"


def test_bastion_counts_fully_sized_decisions(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", stop_price=100, position_pct=5)   # sized
    _buy(j, "AMD")                                     # not sized
    snap = build_snapshot(storage)
    assert snap.agents["risk-manager"].metric_value == 50.0


def test_bastion_ignores_pass_decisions(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", stop_price=100, position_pct=5)
    j.record(Decision(symbol="INTC", decision="PASS", conviction="low",
                      thesis="x" * 40, falsification="y" * 40))
    snap = build_snapshot(storage)
    # Only the one BUY is in the BUY/SELL denominator → 100%, PASS excluded.
    assert snap.agents["risk-manager"].metric_value == 100.0


def test_echo_reuses_journal_scorecard_discipline(storage):
    j = Journal(storage._conn)
    did = _buy(j, "PLTR", stop_price=20, position_pct=3)
    j.review(did, outcome="right", exit_price=40, pnl_pct=52, followed_own_rules=True,
             lessons="as planned")
    snap = build_snapshot(storage)
    assert snap.agents["behavioral-coach"].metric_value == 100.0
    assert snap.agents["behavioral-coach"].status == "live"


def test_echo_no_data_until_a_decision_is_reviewed(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", stop_price=90, position_pct=5)  # open, never reviewed
    snap = build_snapshot(storage)
    assert snap.agents["behavioral-coach"].status == "no_data"


def test_ledger_counts_buys_only(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA")
    _buy(j, "AMD")
    j.record(Decision(symbol="INTC", decision="PASS", conviction="low",
                      thesis="x" * 40, falsification="y" * 40))
    snap = build_snapshot(storage)
    assert snap.agents["equity-analyst"].metric_value == 2.0


def test_cipher_reads_latest_sweep_hit_rate(storage):
    storage.record_sweep_result(
        run_id="r1", strategy_key="rsi_mean_reversion", symbols_tested=24, wins=8,
        hit_rate_pct=33.0, median_excess_pts=-37.4, total_trades=185,
        window_start="2021-01-01",
    )
    snap = build_snapshot(storage)
    assert snap.agents["quant-analyst"].metric_value == 33.0
    assert snap.agents["quant-analyst"].status == "live"


def test_ember_is_labeled_proxy_and_counts_high_conviction(storage):
    j = Journal(storage._conn)
    _buy(j, "ASTS", conviction="high")
    _buy(j, "NVDA", conviction="medium")
    snap = build_snapshot(storage)
    r = snap.agents["special-situations"]
    assert r.status == "proxy"
    assert r.metric_value == 1.0
    assert "proxy" in r.metric_label.lower()


def test_radar_counts_live_enabled_only(storage):
    storage.upsert_watchlist_symbol("NVDA", "momentum_90d", live_enabled=True, rank=0)
    storage.upsert_watchlist_symbol("TSLA", "rsi_mean_reversion", live_enabled=False, rank=1)
    snap = build_snapshot(storage)
    assert snap.agents["setup-scanner"].metric_value == 1.0


def test_compass_averages_upside_and_skips_incomplete_rows(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", entry_price=100, target_price=150)  # +50%
    _buy(j, "AMD", entry_price=100, target_price=120)   # +20%
    _buy(j, "INTC", entry_price=100)                    # no target → skipped
    snap = build_snapshot(storage)
    assert snap.agents["valuation-analyst"].metric_value == pytest.approx(35.0)


def test_talon_counts_red_team_objections(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", red_team_objection="the bear case")
    _buy(j, "AMD")  # no objection
    snap = build_snapshot(storage)
    assert snap.agents["red-team"].metric_value == 50.0


# --- portfolio panel --------------------------------------------------------

def test_portfolio_panel_none_without_contributions(storage):
    storage.record_equity_snapshot(equity=5000.0, cash=1000.0)
    snap = build_snapshot(storage)
    assert snap.portfolio is None


def test_portfolio_panel_none_without_equity_snapshot(storage):
    clog = ContributionLog(storage._conn)
    clog.add(Contribution("2026-01-05", 5000.0))
    # No equity snapshot recorded → panel stays None even with a contribution.
    snap = build_snapshot(storage)
    assert snap.portfolio is None


# --- render smoke -----------------------------------------------------------

def test_render_static_is_self_contained(storage):
    html = render_html(build_snapshot(storage), mode="static")
    assert "<script src" not in html          # no external scripts
    assert "fetch(" not in html                # no network calls in static mode
    for codename in ["APEX", "LATTICE", "TALON", "HORIZON"]:
        assert codename in html
    assert "NO SIGNAL" in html                 # empty DB → HORIZON at least


def test_render_live_mode_has_the_poll(storage):
    html = render_html(build_snapshot(storage), mode="live")
    assert 'fetch("/api/snapshot.json"' in html
    assert "__CORTEX_APPLY__" in html


def test_render_embeds_the_disclaimer(storage):
    html = render_html(build_snapshot(storage), mode="static")
    assert "not a trained model" in html


# --- committee activity -----------------------------------------------------

def test_committee_none_when_no_run(storage):
    snap = build_snapshot(storage)
    assert snap.committee is None


def test_committee_active_when_run_is_open_and_recent(storage):
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="ASTS")
    storage.record_committee_event("r1", "cio", "handoff", "delegating",
                                   to_agent="red-team")
    snap = build_snapshot(storage)
    c = snap.committee
    assert c["state"] == "active"
    # After a handoff, the target is the one now working.
    assert c["active_agent"] == "red-team"
    assert c["symbol"] == "ASTS"
    assert c["concluded"] is False


def test_committee_complete_when_memo_issued(storage):
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="NVDA")
    storage.record_committee_event("r1", "red-team", "verdict", "survivable")
    storage.record_committee_event("r1", "cio", "memo", "BUY, sized small")
    snap = build_snapshot(storage)
    c = snap.committee
    assert c["state"] == "complete"
    assert c["concluded"] is True
    assert c["active_agent"] is None


def test_committee_symbol_inherited_across_events(storage):
    # Only the first event carries the symbol; later events inherit it.
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="ASTS")
    storage.record_committee_event("r1", "macro-strategist", "finding", "late cycle")
    events = storage.committee_run_events("r1")
    assert all(e["symbol"] == "ASTS" for e in events)


def test_committee_active_agent_is_actor_when_last_event_not_handoff(storage):
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="ASTS")
    storage.record_committee_event("r1", "equity-analyst", "finding", "moat intact")
    snap = build_snapshot(storage)
    assert snap.committee["active_agent"] == "equity-analyst"


def test_stale_open_run_is_idle_not_active(storage):
    # Backdate the events beyond the active window by writing directly.
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="ASTS")
    storage._conn.execute(
        "UPDATE committee_events SET logged_at = ? WHERE run_id = 'r1'",
        ("2020-01-01T00:00:00+00:00",),
    )
    storage._conn.commit()
    snap = build_snapshot(storage)
    assert snap.committee["state"] == "idle"


def test_memory_grows_with_runs_and_decisions(storage):
    j = Journal(storage._conn)
    _buy(j, "NVDA", stop_price=90, position_pct=5)
    storage.record_committee_event("r1", "cio", "start", "opening", symbol="NVDA")
    storage.record_committee_event("r1", "cio", "memo", "BUY")
    snap = build_snapshot(storage)
    assert snap.memory["committee_runs"] == 1
    assert snap.memory["decisions_logged"] == 1


def test_render_shows_committee_activity_and_memory_panels(storage):
    storage.record_committee_event("r1", "cio", "start", "opening review", symbol="ASTS")
    storage.record_committee_event("r1", "cio", "memo", "BUY ASTS")
    html = render_html(build_snapshot(storage), mode="static")
    assert "Committee activity" in html
    assert "Collective memory" in html


# --- collective memory (shared-brain ledger) --------------------------------

def test_memory_episode_round_trip(storage):
    storage.record_memory_episode(
        kind="decision", title="ASTS: BUY", body="sized small, 2026-08-14",
        symbol="ASTS", run_id="r1",
    )
    eps = storage.recent_memory_episodes()
    assert len(eps) == 1
    assert eps[0]["symbol"] == "ASTS"
    assert eps[0]["mirrored_to_brain"] == 0


def test_memory_recall_matches_symbol_and_body(storage):
    storage.record_memory_episode(kind="decision", title="ASTS: BUY",
                                  body="spectrum moat", symbol="ASTS")
    storage.record_memory_episode(kind="finding", title="NVDA note",
                                  body="datacenter demand", symbol="NVDA")
    hits = storage.search_memory_episodes("spectrum")
    assert len(hits) == 1 and hits[0]["symbol"] == "ASTS"
    by_symbol = storage.search_memory_episodes("NVDA")
    assert len(by_symbol) == 1 and by_symbol[0]["symbol"] == "NVDA"


def test_memory_recall_filters_by_symbol_scope(storage):
    storage.record_memory_episode(kind="decision", title="a", body="x", symbol="ASTS")
    storage.record_memory_episode(kind="decision", title="b", body="y", symbol="NVDA")
    assert len(storage.recent_memory_episodes(symbol="ASTS")) == 1


def test_snapshot_memory_counts_episodes_and_mirroring(storage):
    storage.record_memory_episode(kind="decision", title="a", body="x", mirrored_to_brain=True)
    storage.record_memory_episode(kind="finding", title="b", body="y", mirrored_to_brain=False)
    m = build_snapshot(storage).memory
    assert m["episodes_count"] == 2
    assert m["episodes_mirrored"] == 1
    assert len(m["recent_episodes"]) == 2


def test_empty_memory_has_zero_episodes(storage):
    m = build_snapshot(storage).memory
    assert m["episodes_count"] == 0
    assert m["recent_episodes"] == []


def test_render_shows_shared_brain_episodes(storage):
    storage.record_memory_episode(kind="decision", title="ASTS: BUY sized small",
                                  body="x", symbol="ASTS", mirrored_to_brain=False)
    html = render_html(build_snapshot(storage), mode="static")
    assert "shared-brain episodes" in html
    assert "ASTS: BUY sized small" in html


# --- live server (threaded) -------------------------------------------------

def test_dashboard_server_serves_across_threads(tmp_path):
    """Regression: the local server handles each request in its own thread, and
    a SQLite connection can't cross threads — so each request must open its own.
    A shared connection would crash the handler and return an empty response."""
    import json
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    db_path = str(tmp_path / "srv.db")
    seed = Storage(db_path)
    seed.record_committee_event("r1", "cio", "start", "opening", symbol="ASTS")
    seed.close()

    # Mirror exactly what the dashboard command does: a fresh Storage per request.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            s = Storage(db_path)
            try:
                snap = build_snapshot(s)
            finally:
                s.close()
            body = json.dumps({"agents": len(snap.agents),
                               "committee": snap.committee["state"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot.json", timeout=5)
        assert r.status == 200
        data = json.loads(r.read())
        assert data["agents"] == 11
        assert data["committee"] == "active"
    finally:
        server.shutdown()
        server.server_close()


# --- command deck / Option 2 ------------------------------------------------

def test_parse_review_command_extracts_ticker():
    assert parse_review_command("review ASTS") == "ASTS"
    assert parse_review_command("ASTS") == "ASTS"
    assert parse_review_command("run a review on NVDA") == "NVDA"
    assert parse_review_command("brk.b") == "BRK.B"


def test_parse_review_command_rejects_empty_and_wordy():
    with pytest.raises(CommandError):
        parse_review_command("")
    with pytest.raises(CommandError):
        parse_review_command("   ")


def test_review_prompt_contains_validated_symbol_only():
    p = review_prompt("ASTS")
    assert "ASTS" in p
    assert "cio agent" in p
    # the prompt is built from the symbol, never raw user text
    assert "log-event" in p


def test_enqueue_and_pending_and_resolve(storage):
    cid = storage.enqueue_command("review", review_prompt("ASTS"), symbol="ASTS")
    pending = storage.pending_commands()
    assert len(pending) == 1 and pending[0]["symbol"] == "ASTS"
    storage.update_command(cid, status="running", detail="claude started")
    assert storage.pending_commands() == []
    got = storage.get_command(cid)
    assert got["status"] == "running" and got["detail"] == "claude started"


def test_snapshot_includes_recent_commands(storage):
    storage.enqueue_command("review", review_prompt("ASTS"), symbol="ASTS")
    storage.enqueue_command("review", review_prompt("NVDA"), symbol="NVDA")
    snap = build_snapshot(storage)
    syms = [c["symbol"] for c in snap.commands]
    assert "ASTS" in syms and "NVDA" in syms


def test_render_shows_command_deck(storage):
    storage.enqueue_command("review", review_prompt("ASTS"), symbol="ASTS")
    html = render_html(build_snapshot(storage), mode="static")
    assert "Command deck" in html
    assert "COMMAND" in html  # the command bar prompt


# --- dashboard access token -------------------------------------------------

def test_token_match_disabled_when_no_token():
    from hf_trading_bot.cli import _token_match
    authed, via_query = _token_match("")
    assert authed is True and via_query is False


def test_token_match_via_query_signals_cookie():
    from hf_trading_bot.cli import _token_match
    authed, via_query = _token_match("s3cret", query="key=s3cret")
    assert authed is True and via_query is True


def test_token_match_via_cookie_and_header():
    from hf_trading_bot.cli import _token_match
    assert _token_match("s3cret", cookie="cortex_key=s3cret") == (True, False)
    assert _token_match("s3cret", header="s3cret") == (True, False)
    # other cookies alongside the right one still authorize
    assert _token_match("s3cret", cookie="foo=bar; cortex_key=s3cret")[0] is True


def test_token_match_rejects_wrong_or_missing():
    from hf_trading_bot.cli import _token_match
    assert _token_match("s3cret", query="key=nope") == (False, False)
    assert _token_match("s3cret", cookie="cortex_key=nope") == (False, False)
    assert _token_match("s3cret") == (False, False)
