"""Cortex data layer + a light render smoke test. All offline, no network."""
import sqlite3

import pytest

from hf_trading_bot.cortex import build_snapshot
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
