"""Tests for the VELOCITY agent swarm.

The interesting ones are at the bottom: the invariants that catch a system
quietly cheating itself (look-ahead, accounting drift, a risk gate that can
be outvoted, a null test that comes back positive).
"""
from __future__ import annotations

import os
import statistics

import pytest

from velocity.agents.arbiter import Arbiter
from velocity.agents.chartist import Chartist
from velocity.agents.quant import Quant
from velocity.agents.sentinel import Sentinel, score_headline
from velocity.agents.warden import RiskLimits, Warden
from velocity.bus import Blackboard
from velocity.engine import Engine, LatencyRecorder
from velocity.execution import ExecConfig, PaperExecutor
from velocity.feeds import SyntheticFeed
from velocity.indicators import (EMA, VWAP, RollingExtrema, RollingStats,
                                 StreamATR, StreamRSI)
from velocity.types import NS_PER_S, Decision, Side, Signal, Tick


# ---------------------------------------------------------------- indicators

def test_rolling_stats_matches_reference():
    s = RollingStats(5)
    for x in range(1, 11):
        s.update(float(x))
    assert s.mean == pytest.approx(8.0)
    assert s.std == pytest.approx(statistics.pstdev([6, 7, 8, 9, 10]))


def test_rolling_stats_recompute_keeps_precision():
    """Running sums drift; the periodic recompute must hold them straight."""
    s = RollingStats(10, recompute_every=50)
    for i in range(5_000):
        s.update(1e6 + (i % 7))
    ref = list(s.buf)
    assert s.mean == pytest.approx(sum(ref) / len(ref), rel=1e-12)


def test_rolling_extrema_tracks_window():
    e = RollingExtrema(3)
    for v in (5, 1, 9, 2, 2, 2):
        e.update(v)
    assert e.max == 2 and e.min == 2
    e2 = RollingExtrema(4)
    for v in (1, 7, 3, 4):
        e2.update(v)
    assert e2.max == 7 and e2.min == 1


def test_rsi_bounds_and_extremes():
    r = StreamRSI(14)
    for i in range(60):
        v = r.update(100.0 + i)        # monotonic up
    assert v > 95.0
    r2 = StreamRSI(14)
    for i in range(60):
        v2 = r2.update(100.0 - i)      # monotonic down
    assert v2 < 5.0


def test_vwap_weights_by_size_and_falls_back():
    v = VWAP()
    v.update(100.0, 1.0)
    v.update(200.0, 3.0)
    assert v.value == pytest.approx(175.0)
    v2 = VWAP()                        # size-less feed -> plain mean
    v2.update(10.0, 0.0)
    v2.update(20.0, 0.0)
    assert v2.value == pytest.approx(15.0)


def test_ema_converges():
    e = EMA(10)
    for _ in range(200):
        e.update(42.0)
    assert e.value == pytest.approx(42.0)


# ------------------------------------------------------------------- signals

def test_signal_freshness_decays_to_zero_at_ttl():
    s = Signal("X", 0, 1.0, 1.0, 1000)
    assert s.freshness(0) == 1.0
    assert s.freshness(500) == pytest.approx(0.5)
    assert s.freshness(1000) == 0.0
    assert s.freshness(99_999) == 0.0
    assert not s.alive(1001)


def test_blackboard_read_zeroes_dead_signals():
    bb = Blackboard("T")
    bb.publish(Signal("QUANT", 0, 1.0, 1.0, 1000))
    assert bb.read("QUANT", 0) == pytest.approx(1.0)
    assert bb.read("QUANT", 2000) == 0.0          # dead, not merely faint
    assert bb.read("NOBODY", 0) == 0.0


def test_signal_clamps_out_of_range_inputs():
    s = Signal("X", 0, 9.0, 9.0, 10)
    assert s.strength == 1.0 and s.confidence == 1.0


# ------------------------------------------------------------------ sentinel

@pytest.mark.parametrize("text,expect_sign", [
    ("Acme cuts guidance and warns on demand", -1),
    ("Acme beats estimates, raises guidance", 1),
    ("Acme announces a routine office relocation", 0),
])
def test_headline_scoring_direction(text, expect_sign):
    score, _halt, _ = score_headline(text)
    assert (score > 0.1) - (score < -0.1) == expect_sign


def test_halt_words_flagged():
    _, halt, _ = score_headline("Trading halted in Acme pending news")
    assert halt is True


def test_news_confidence_decays_to_zero():
    s = Sentinel("ACME", half_life_s=180.0)
    s.ingest(["Acme cuts guidance"], now_s=0.0)
    _, c_fresh, _ = s.aggregate(now_s=0.0)
    _, c_old, _ = s.aggregate(now_s=600.0)
    _, c_dead, _ = s.aggregate(now_s=5_000.0)
    assert c_fresh > c_old > c_dead
    assert c_dead == 0.0


def test_corroboration_raises_confidence():
    one = Sentinel("A"); one.ingest(["Acme cuts guidance"], now_s=0.0)
    many = Sentinel("A")
    many.ingest(["Acme cuts guidance", "Acme warns on demand", "Acme downgraded"], now_s=0.0)
    assert many.aggregate(now_s=0.0)[1] > one.aggregate(now_s=0.0)[1]


def test_sentinel_survives_a_broken_feed():
    """A news source that throws must not be able to stop trading."""
    s = Sentinel("A", feed_url="http://127.0.0.1:9/nope", timeout_s=0.01)
    assert s.fetch() == []
    assert s.last_error
    sig = s.poll(Blackboard("A"))
    assert sig is not None and sig.confidence == 0.0


def test_duplicate_headlines_are_not_double_counted():
    s = Sentinel("A")
    assert s.ingest(["Acme cuts guidance"], now_s=0.0) == 1
    assert s.ingest(["Acme cuts guidance"], now_s=1.0) == 0


# -------------------------------------------------------------------- warden

def _tick(ts_s: float, px: float = 100.0, spread_bps: float = 1.0) -> Tick:
    half = px * spread_bps / 20_000.0
    return Tick(int(ts_s * NS_PER_S), "T", px, 100.0, px - half, px + half)


def _warm(w: Warden, bb: Blackboard, n: int = 400, px: float = 100.0) -> Tick:
    t = _tick(0)
    for i in range(n):
        t = _tick(i * 0.1, px + (i % 5) * 0.01)
        bb.mark(t.price, t.ts_ns)
        w.on_tick(t, bb)
    return t


def test_warden_vetoes_on_daily_loss_limit():
    bb = Blackboard("T", equity=100_000.0)
    w = Warden(RiskLimits(daily_loss_limit=0.01), start_equity=100_000.0)
    t = _warm(w, bb)
    bb.realised_pnl = -1_500.0
    v = w.review(1, t, bb)
    assert not v.allow and v.flatten and bb.halted


def test_warden_vetoes_on_wide_spread():
    bb = Blackboard("T")
    w = Warden(RiskLimits(max_spread_bps=5.0, cooldown_s=0.0), start_equity=100_000.0)
    _warm(w, bb)
    wide = _tick(100.0, 100.0, spread_bps=50.0)
    assert not w.review(1, wide, bb).allow


def test_warden_enforces_cooldown():
    bb = Blackboard("T")
    w = Warden(RiskLimits(cooldown_s=30.0), start_equity=100_000.0)
    t = _warm(w, bb)
    bb.last_exit_ns = t.ts_ns - int(5 * NS_PER_S)
    v = w.review(1, t, bb)
    assert not v.allow and "cooldown" in v.reason


def test_warden_kill_switch(tmp_path, monkeypatch):
    kill = tmp_path / "kill"
    bb = Blackboard("T")
    w = Warden(RiskLimits(kill_switch_path=str(kill), cooldown_s=0.0), start_equity=100_000.0)
    t = _warm(w, bb)
    assert w.review(1, t, bb).allow
    kill.write_text("stop")
    bb.tick_count += 1000          # force the periodic re-check
    v = w.review(1, t, bb)
    assert not v.allow and v.flatten and "KILL" in v.reason


def test_warden_size_falls_as_volatility_rises():
    """Volatility targeting: the same risk budget must buy less in a storm."""
    sizes = []
    for amp in (0.01, 0.50):
        bb = Blackboard("T")
        w = Warden(RiskLimits(cooldown_s=0.0), start_equity=100_000.0)
        t = _tick(0)
        for i in range(600):
            t = _tick(i * 0.1, 100.0 + (i % 2) * amp)
            bb.mark(t.price, t.ts_ns)
            w.on_tick(t, bb)
        v = w.review(1, t, bb)
        sizes.append(v.max_notional if v.allow else 0.0)
    assert sizes[1] < sizes[0]


def test_stop_scales_with_holding_horizon():
    """The bug that made every trade last 0.6s: stops sized per-tick."""
    bb = Blackboard("T")
    w_short = Warden(RiskLimits(horizon_ticks=1), start_equity=100_000.0)
    w_long = Warden(RiskLimits(horizon_ticks=400), start_equity=100_000.0)
    for w in (w_short, w_long):
        _warm(w, Blackboard("T"))
    assert w_long.vol_horizon > w_short.vol_horizon * 15


# ------------------------------------------------------------------- arbiter

class _StubWarden(Warden):
    def review(self, side, tick, bb):
        from velocity.types import Verdict
        return Verdict(True, 10_000.0, "stub")

    def stop_and_target(self, side, price):
        return (price - 1.0, price + 2.0) if side > 0 else (price + 1.0, price - 2.0)


def test_arbiter_requires_confluence():
    """One loud agent must not be able to open a position on its own."""
    a = Arbiter(min_agreeing=2, entry_threshold=0.2)
    bb = Blackboard("T")
    t = _tick(10)
    bb.mark(t.price, t.ts_ns)
    bb.publish(Signal("QUANT", t.ts_ns, 1.0, 1.0, NS_PER_S * 10))
    d = a.decide(t, bb, _StubWarden())
    assert not d.is_trade and "confluence" in d.reason

    bb.publish(Signal("CHARTIST", t.ts_ns, 0.9, 1.0, NS_PER_S * 10))
    d2 = a.decide(t, bb, _StubWarden())
    assert d2.is_trade and d2.side is Side.LONG


def test_sentinel_veto_blocks_opposing_trade():
    a = Arbiter(min_agreeing=2, entry_threshold=0.2, news_veto_level=0.5)
    bb = Blackboard("T")
    t = _tick(10)
    bb.mark(t.price, t.ts_ns)
    bb.publish(Signal("QUANT", t.ts_ns, 1.0, 1.0, NS_PER_S * 10))
    bb.publish(Signal("CHARTIST", t.ts_ns, 1.0, 1.0, NS_PER_S * 10))
    bb.publish(Signal("SENTINEL", t.ts_ns, -1.0, 0.9, NS_PER_S * 600))
    d = a.decide(t, bb, _StubWarden())
    assert not d.is_trade and "SENTINEL veto" in d.reason


def test_warden_veto_cannot_be_outvoted():
    """The whole point of a hard gate: unanimous agents still lose to risk."""
    class _NoWarden(Warden):
        def review(self, side, tick, bb):
            from velocity.types import Verdict
            return Verdict(False, 0.0, "nope")

    a = Arbiter(min_agreeing=2, entry_threshold=0.1)
    bb = Blackboard("T")
    t = _tick(10)
    bb.mark(t.price, t.ts_ns)
    for name in ("QUANT", "CHARTIST", "SENTINEL"):
        bb.publish(Signal(name, t.ts_ns, 1.0, 1.0, NS_PER_S * 10))
    d = a.decide(t, bb, _NoWarden())
    assert not d.is_trade and "WARDEN veto" in d.reason


def test_disagreement_shrinks_size():
    bb_a, bb_b = Blackboard("T"), Blackboard("T")
    t = _tick(10)
    for bb, chart in ((bb_a, 0.9), (bb_b, 0.05)):
        bb.mark(t.price, t.ts_ns)
        bb.publish(Signal("QUANT", t.ts_ns, 0.9, 1.0, NS_PER_S * 10))
        bb.publish(Signal("CHARTIST", t.ts_ns, chart, 1.0, NS_PER_S * 10))
    agree = Arbiter(min_agreeing=1, entry_threshold=0.1).decide(t, bb_a, _StubWarden())
    split = Arbiter(min_agreeing=1, entry_threshold=0.1).decide(t, bb_b, _StubWarden())
    assert agree.notional > split.notional


def test_stale_signals_lose_their_vote():
    a = Arbiter(min_agreeing=2, entry_threshold=0.2)
    bb = Blackboard("T")
    t_old = _tick(0)
    bb.publish(Signal("QUANT", t_old.ts_ns, 1.0, 1.0, NS_PER_S))
    bb.publish(Signal("CHARTIST", t_old.ts_ns, 1.0, 1.0, NS_PER_S))
    later = _tick(60)
    bb.mark(later.price, later.ts_ns)
    assert not a.decide(later, bb, _StubWarden()).is_trade


# ----------------------------------------------------------------- execution

def test_fill_is_always_worse_than_mid():
    """A simulator that fills at the mid is reporting an impossible trade."""
    ex = PaperExecutor(ExecConfig(seed=1))
    bb = Blackboard("T")
    t = _tick(0, 100.0, spread_bps=10.0)
    d = Decision(t.ts_ns, "T", Side.LONG, 10_000.0, t.price, 99.0, 102.0)
    f = ex.open(d, t, bb, vol_unit=0.01)
    assert f.price > t.price          # bought above mid
    f2 = ex.close(t, bb, "test", vol_unit=0.01)
    assert f2.price < t.price         # sold below mid


def test_stop_is_checked_every_tick():
    ex = PaperExecutor(ExecConfig(seed=1))
    bb = Blackboard("T")
    t = _tick(0, 100.0)
    ex.open(Decision(t.ts_ns, "T", Side.LONG, 10_000.0, 100.0, 99.0, 103.0), t, bb, 0.01)
    assert ex.manage(_tick(1, 99.5), bb, 0.01) is None
    f = ex.manage(_tick(2, 98.9), bb, 0.01)
    assert f is not None and ex.trades[-1].reason_out == "stop"


def test_short_side_pnl_sign_is_correct():
    ex = PaperExecutor(ExecConfig(latency_ms=0, slippage_bps=0, assume_spread_bps=0, seed=1))
    bb = Blackboard("T")
    t = _tick(0, 100.0, spread_bps=0.0)
    ex.open(Decision(t.ts_ns, "T", Side.SHORT, 10_000.0, 100.0, 102.0, 95.0), t, bb, 0.0)
    ex.close(_tick(1, 95.0, spread_bps=0.0), bb, "target", 0.0)
    assert ex.trades[-1].pnl > 0      # short + price fell = profit


def test_consecutive_losses_tracked_and_reset():
    ex = PaperExecutor(ExecConfig(latency_ms=0, slippage_bps=0, assume_spread_bps=0, seed=1))
    bb = Blackboard("T")
    for px_out, _ in ((99.0, 0), (98.0, 0)):
        t = _tick(0, 100.0, spread_bps=0.0)
        ex.open(Decision(t.ts_ns, "T", Side.LONG, 1_000.0, 100.0, 90.0, 110.0), t, bb, 0.0)
        ex.close(_tick(1, px_out, spread_bps=0.0), bb, "stop", 0.0)
    assert bb.consecutive_losses == 2
    t = _tick(0, 100.0, spread_bps=0.0)
    ex.open(Decision(t.ts_ns, "T", Side.LONG, 1_000.0, 100.0, 90.0, 110.0), t, bb, 0.0)
    ex.close(_tick(1, 105.0, spread_bps=0.0), bb, "target", 0.0)
    assert bb.consecutive_losses == 0


# -------------------------------------------------------------------- engine

def test_engine_runs_and_ends_flat():
    eng = Engine("SYNTH", equity=50_000.0)
    r = eng.run(SyntheticFeed(n_ticks=20_000, seed=3))
    assert r["ticks"] == 20_000
    assert eng.bb.flat, "engine must never finish holding an open position"
    assert eng.executor.open_trade is None


def test_accounting_balances():
    """Equity must equal start + sum of trade P&L. No money appears."""
    eng = Engine("SYNTH", equity=100_000.0)
    eng.run(SyntheticFeed(n_ticks=50_000, seed=9))
    booked = sum(t.pnl for t in eng.executor.trades)
    assert eng.bb.equity == pytest.approx(100_000.0 + booked, rel=1e-9)
    assert eng.bb.realised_pnl == pytest.approx(booked, rel=1e-9)


def test_costs_are_never_negative():
    eng = Engine("SYNTH", equity=100_000.0)
    eng.run(SyntheticFeed(n_ticks=50_000, seed=4))
    assert eng.executor.total_costs >= 0.0
    for t in eng.executor.trades:
        assert t.costs >= 0.0


def test_no_lookahead_price_never_exceeds_tick():
    """Every fill must be struck at a price derived from the tick that
    caused it — never a later one. Replaying a truncated stream must
    reproduce the identical prefix of trades."""
    # Both the feed AND the fill model must be seeded, or the comparison
    # measures RNG divergence rather than look-ahead.
    full = Engine("SYNTH", equity=100_000.0, exec_cfg=ExecConfig(seed=77))
    full.run(SyntheticFeed(n_ticks=40_000, seed=15))
    short = Engine("SYNTH", equity=100_000.0, exec_cfg=ExecConfig(seed=77))
    short.run(SyntheticFeed(n_ticks=40_000, seed=15), max_ticks=20_000)
    n = len(short.executor.trades) - 1      # last one is force-closed at cutoff
    assert n > 0, "need some trades to compare"
    for i in range(n):
        assert full.executor.trades[i].entry == pytest.approx(short.executor.trades[i].entry)
        assert full.executor.trades[i].exit == pytest.approx(short.executor.trades[i].exit)


def test_null_test_finds_no_edge_in_noise():
    """THE test. On a driftless random walk the swarm must not profit.

    A positive result here means a look-ahead leak or an accounting bug,
    not a discovery. It is the first thing to check after any change.
    """
    pre_costs = []
    for seed in (11, 23, 77, 101, 202):
        eng = Engine("NULL", equity=100_000.0)
        r = eng.run(SyntheticFeed(symbol="NULL", n_ticks=60_000, seed=seed, drift_scale=0.0))
        if r.get("trades", 0) >= 5:
            pre_costs.append(r["pnl_before_costs"])
    assert pre_costs, "null test produced no trades to evaluate"
    mean_pre = sum(pre_costs) / len(pre_costs)
    assert mean_pre <= 0.002 * 100_000, (
        f"swarm made ${mean_pre:,.0f} before costs on pure noise — "
        "suspect look-ahead or an accounting leak"
    )


def test_halt_is_sticky_and_flattens():
    eng = Engine("SYNTH", equity=100_000.0,
                 limits=RiskLimits(daily_loss_limit=0.0001))
    eng.run(SyntheticFeed(n_ticks=60_000, seed=8))
    assert eng.bb.flat


def test_latency_recorder_percentiles():
    L = LatencyRecorder(cap=1000)
    for i in range(1000):
        L.record(i)
    s = L.summary()
    assert s["p50_ns"] == pytest.approx(499, abs=2)
    assert s["p99_ns"] == pytest.approx(989, abs=3)
    assert s["max_ns"] == 999


def test_latency_recorder_ring_does_not_grow():
    L = LatencyRecorder(cap=100)
    for i in range(10_000):
        L.record(i)
    assert len(L.buf) == 100
    assert L.summary()["n"] == 10_000


def test_hot_loop_stays_under_budget():
    """Guards against someone putting I/O or an allocation in on_tick."""
    eng = Engine("SYNTH", equity=100_000.0)
    r = eng.run(SyntheticFeed(n_ticks=30_000, seed=6))
    assert r["latency"]["p99_ns"] < 2_000_000, "p99 above 2ms — something slow got into the hot path"


def test_agents_warm_up_before_signalling():
    """An agent with no data must say so with zero confidence rather than
    emit a confident number out of an unwarmed indicator."""
    bb = Blackboard("T")
    q, c = Quant(), Chartist()
    t = _tick(0)
    bb.mark(t.price, t.ts_ns)
    assert q.on_tick(t, bb).confidence == 0.0
    assert c.on_tick(t, bb).confidence == 0.0
