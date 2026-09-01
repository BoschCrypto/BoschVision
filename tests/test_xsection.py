"""Cross-sectional Gated Momentum engine. Synthetic bars, no network.

The centrepiece here is `test_no_lookahead_future_prices_cannot_change_history`:
a backtest that peeks at future prices produces flattering numbers and no error
message, so the property has to be asserted directly rather than assumed from
reading the code.
"""
from __future__ import annotations

import math

import pytest

from hf_trading_bot import factors
from hf_trading_bot.data.bars import Bar
from hf_trading_bot.xsection import (
    GatedMomentumParams,
    UniverseMode,
    buy_and_hold,
    integrity_warnings,
    run,
    run_ablations,
)


# --- builders --------------------------------------------------------------

def _dates(n: int, start_year: int = 2015) -> list[str]:
    """`n` sequential weekday-ish dates. Calendar exactness does not matter —
    month boundaries do, because they drive rebalances."""
    out: list[str] = []
    y, m, d = start_year, 1, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > 28:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
    return out


def _bars(dates: list[str], prices: list[float], volume: float = 5_000_000.0) -> list[Bar]:
    return [
        Bar(t=t, o=p, h=p * 1.01, l=p * 0.99, c=p, v=volume)
        for t, p in zip(dates, prices)
    ]


def _trend(dates: list[str], start: float, daily: float, volume: float = 5_000_000.0) -> list[Bar]:
    prices = [start * ((1 + daily) ** i) for i in range(len(dates))]
    return _bars(dates, prices, volume)


# --- factor primitives -----------------------------------------------------

class TestFactors:
    def test_momentum_skips_the_recent_month(self):
        dates = _dates(300)
        # Flat for the first year, then a spike inside the skipped window only.
        prices = [100.0] * 280 + [200.0] * 20
        bars = _bars(dates, prices)
        mom = factors.momentum_12_1(bars, 299)
        # Window is [299-252] -> [299-21] = index 47 -> 278, both still flat.
        assert mom == pytest.approx(0.0)

    def test_momentum_requires_full_history(self):
        bars = _bars(_dates(100), [100.0] * 100)
        assert factors.momentum_12_1(bars, 99) is None

    def test_pct_of_52w_high_at_the_high(self):
        dates = _dates(300)
        bars = _trend(dates, 100.0, 0.001)
        assert factors.pct_of_52w_high(bars, 299) == pytest.approx(1.0)

    def test_pct_of_52w_high_after_giving_back_a_spike(self):
        dates = _dates(300)
        prices = [100.0] * 250 + [150.0] * 10 + [105.0] * 40
        bars = _bars(dates, prices)
        prox = factors.pct_of_52w_high(bars, 299)
        assert prox == pytest.approx(105.0 / 150.0)
        # This is exactly the case the filter exists to reject: strong 12-1
        # momentum, but the move has already been given back.
        assert prox < 0.85

    def test_atr_pct_is_scale_free(self):
        dates = _dates(60)
        cheap = _bars(dates, [10.0] * 60)
        rich = _bars(dates, [1000.0] * 60)
        assert factors.atr_pct(cheap, 59) == pytest.approx(factors.atr_pct(rich, 59))

    def test_month_end_indices_find_boundaries(self):
        dates = ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02"]
        bars = _bars(dates, [1.0] * 5)
        assert factors.month_end_indices(bars) == [1, 3]


class TestRegimeGate:
    def _spy(self, n: int, daily: float) -> list[Bar]:
        return _trend(_dates(n), 100.0, daily)

    def test_uptrend_is_risk_on(self):
        spy = self._spy(900, 0.0008)
        assert factors.regime_risk_on(spy, 899) is True

    def test_downtrend_is_risk_off(self):
        spy = self._spy(900, -0.0008)
        assert factors.regime_risk_on(spy, 899) is False

    def test_insufficient_history_is_none_not_true(self):
        # Callers must treat None as "cannot evaluate" and stay in cash. If this
        # ever returned True the gate would silently fail open.
        spy = self._spy(200, 0.001)
        assert factors.regime_risk_on(spy, 199) is None


class TestInverseVolWeights:
    def test_lower_vol_gets_more_weight(self):
        # Uncapped, so the vol relationship is visible on its own. With the
        # default 15% cap two names could not both fit under it and the
        # infeasible-cap fallback below would equalise them.
        w = factors.inverse_vol_weights({"CALM": 0.01, "WILD": 0.04}, max_weight=1.0)
        assert w["CALM"] > w["WILD"]
        assert w["CALM"] == pytest.approx(0.8)
        assert sum(w.values()) == pytest.approx(1.0)

    def test_cap_is_respected_after_redistribution(self):
        # One very low-vol name would otherwise take almost the whole book.
        w = factors.inverse_vol_weights(
            {"A": 0.001, "B": 0.05, "C": 0.05, "D": 0.05, "E": 0.05, "F": 0.05,
             "G": 0.05, "H": 0.05, "I": 0.05, "J": 0.05},
            max_weight=0.15,
        )
        assert sum(w.values()) == pytest.approx(1.0)
        assert max(w.values()) <= 0.15 + 1e-9

    def test_impossible_cap_falls_back_to_equal_weight(self):
        w = factors.inverse_vol_weights({"A": 0.01, "B": 0.02, "C": 0.03}, max_weight=0.1)
        assert all(v == pytest.approx(1 / 3) for v in w.values())

    def test_zero_and_negative_vols_are_dropped(self):
        w = factors.inverse_vol_weights({"OK": 0.02, "ZERO": 0.0, "NEG": -1.0})
        assert set(w) == {"OK"}


# --- engine ----------------------------------------------------------------

def _universe_data(n_days: int = 800):
    """SPY plus winners and losers, all liquid enough to pass the screen."""
    dates = _dates(n_days)
    data = {"SPY": _trend(dates, 400.0, 0.0006)}
    for i in range(6):
        data[f"WIN{i}"] = _trend(dates, 50.0 + i, 0.0015, volume=8_000_000.0)
    for i in range(6):
        data[f"LOSE{i}"] = _trend(dates, 80.0 + i, -0.0010, volume=8_000_000.0)
    return dates, data


class TestEngine:
    def test_selects_winners_over_losers(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(
            data, universe, UniverseMode.STATIC_LIST,
            GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000),
        )
        deployed = [r for r in res.rebalances if r.holdings]
        assert deployed, "expected the strategy to deploy in a rising market"
        held = set(deployed[-1].holdings)
        assert all(s.startswith("WIN") for s in held), f"picked losers: {held}"

    def test_weights_sum_to_one_when_deployed(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000))
        for r in res.rebalances:
            if r.holdings:
                assert sum(r.holdings.values()) == pytest.approx(1.0)

    def test_liquidity_screen_excludes_thin_names(self):
        dates, data = _universe_data()
        data["THIN"] = _trend(dates, 30.0, 0.003, volume=1.0)  # best momentum, no volume
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=4, min_dollar_volume=50_000_000))
        for r in res.rebalances:
            assert "THIN" not in r.holdings

    def test_penny_stocks_excluded(self):
        dates, data = _universe_data()
        # Higher momentum than any WIN name and ample volume, so if it is
        # absent from the book only the $10 price floor can have excluded it.
        # Starting low enough that it never clears that floor in the window.
        data["PENNY"] = _trend(dates, 1.0, 0.0018, volume=90_000_000.0)
        assert max(b.c for b in data["PENNY"]) < 10.0
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=4, min_price=10.0,
                                      min_dollar_volume=1_000_000))
        for r in res.rebalances:
            assert "PENNY" not in r.holdings

    def test_regime_gate_moves_to_cash_in_a_bear_market(self):
        dates = _dates(900)
        # Market falls throughout; individual names still have relative winners.
        data = {"SPY": _trend(dates, 400.0, -0.0010)}
        for i in range(4):
            data[f"W{i}"] = _trend(dates, 50.0, 0.0004, volume=9_000_000.0)
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=3, min_dollar_volume=1_000_000))
        assert all(not r.holdings for r in res.rebalances), (
            "regime gate should hold cash while SPY is below trend"
        )
        assert all(r.regime_on is False for r in res.rebalances)

    def test_disabling_the_gate_deploys_in_the_same_bear_market(self):
        dates = _dates(900)
        data = {"SPY": _trend(dates, 400.0, -0.0010)}
        for i in range(4):
            data[f"W{i}"] = _trend(dates, 50.0, 0.0004, volume=9_000_000.0)
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=3, use_regime_gate=False,
                                      min_dollar_volume=1_000_000))
        assert any(r.holdings for r in res.rebalances)

    def test_costs_reduce_equity_versus_frictionless(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        base = GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000)
        with_cost = run(data, universe, UniverseMode.STATIC_LIST,
                        GatedMomentumParams(**{**base.__dict__, "round_trip_bps": 50.0}))
        free = run(data, universe, UniverseMode.STATIC_LIST,
                   GatedMomentumParams(**{**base.__dict__, "round_trip_bps": 0.0}))
        assert with_cost.equity[-1] < free.equity[-1]
        assert with_cost.total_cost_paid > 0
        assert free.total_cost_paid == pytest.approx(0.0)

    def test_max_weight_cap_holds_in_live_run(self):
        # The cap is only satisfiable when the book has more than 1/cap names,
        # so this needs a universe of at least 7 winners to be a real test of
        # the cap rather than of the infeasible-cap fallback.
        dates, data = _universe_data()
        for i in range(6, 12):
            data[f"WIN{i}"] = _trend(dates, 40.0 + i, 0.0012 + i * 0.0001,
                                     volume=8_000_000.0)
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=10, max_weight=0.15,
                                      min_dollar_volume=1_000_000))
        saw_full_book = False
        for r in res.rebalances:
            if r.holdings:
                assert max(r.holdings.values()) <= 0.15 + 1e-9
                saw_full_book = saw_full_book or len(r.holdings) >= 10
        assert saw_full_book, "expected at least one fully-populated book"

    def test_short_history_raises_rather_than_returning_junk(self):
        dates = _dates(100)
        data = {"SPY": _trend(dates, 400.0, 0.001), "A": _trend(dates, 50.0, 0.002)}
        with pytest.raises(ValueError, match="sessions"):
            run(data, ["A"], UniverseMode.STATIC_LIST)

    def test_missing_symbols_are_recorded_not_silently_dropped(self):
        _, data = _universe_data()
        res = run(data, ["WIN0", "GHOST"], UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=2, min_dollar_volume=1_000_000))
        assert "GHOST" in res.skipped_symbols


class TestNoLookahead:
    """The property that matters most, asserted rather than assumed."""

    def test_future_prices_cannot_change_history(self):
        dates, data = _universe_data(800)
        universe = [s for s in data if s != "SPY"]
        params = GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000)

        baseline = run(data, universe, UniverseMode.STATIC_LIST, params)

        # Rewrite the last 30 sessions violently. A backtest that reads forward
        # would reallocate earlier — into or out of the name about to move — and
        # its equity curve before the change would differ.
        tampered = {s: list(b) for s, b in data.items()}
        for s in ("LOSE0", "LOSE1"):
            bars = tampered[s]
            for i in range(len(bars) - 30, len(bars)):
                b = bars[i]
                tampered[s][i] = Bar(t=b.t, o=b.o, h=b.h * 12, l=b.l,
                                     c=b.c * 12, v=b.v)

        after = run(tampered, universe, UniverseMode.STATIC_LIST, params)

        cutoff = len(baseline.dates) - 40
        assert cutoff > 50, "need enough pre-tamper history to make this meaningful"
        for i in range(cutoff):
            assert baseline.equity[i] == pytest.approx(after.equity[i], rel=1e-12), (
                f"equity at {baseline.dates[i]} changed when only FUTURE prices "
                f"were edited — the engine is reading forward"
            )

    def test_signal_and_fill_are_different_sessions(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=3, min_dollar_volume=1_000_000))
        spy_dates = [b.t for b in data["SPY"]]
        month_ends = {spy_dates[i] for i in factors.month_end_indices(data["SPY"])}
        for r in res.rebalances:
            # Fills land on the session *after* a month end, never on it.
            assert r.date not in month_ends


class TestAblations:
    def test_each_ablation_runs_and_is_labelled(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        results = run_ablations(
            data, universe, UniverseMode.STATIC_LIST,
            GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000),
        )
        assert set(results) == {"full", "no_regime_gate", "no_52w_filter",
                                "equal_dollar_weight", "no_cost_model"}
        for name, res in results.items():
            assert res.label == name
            assert res.equity

    def test_equal_dollar_ablation_actually_equal_weights(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        results = run_ablations(
            data, universe, UniverseMode.STATIC_LIST,
            GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000),
            only=["equal_dollar_weight"],
        )
        for r in results["equal_dollar_weight"].rebalances:
            if r.holdings:
                vals = list(r.holdings.values())
                assert all(v == pytest.approx(vals[0]) for v in vals)


class TestIntegrityWarnings:
    def _result(self, **kw):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        params = GatedMomentumParams(
            **{**GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000).__dict__, **kw}
        )
        return run(data, universe, kw.pop("_mode", UniverseMode.STATIC_LIST), params)

    def test_survivorship_bias_is_flagged_loudly(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.CURRENT_MEMBERS,
                  GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000))
        assert any("SURVIVORSHIP BIAS" in w for w in integrity_warnings(res))

    def test_missing_quality_gate_is_flagged(self):
        res = self._result()
        assert any("QUALITY GATE NOT TESTED" in w for w in integrity_warnings(res))

    def test_short_sample_is_flagged(self):
        res = self._result()
        assert any("SHORT SAMPLE" in w or "NARROW WINDOW" in w
                   for w in integrity_warnings(res))

    def test_zero_cost_run_is_flagged(self):
        res = self._result(round_trip_bps=0.0)
        assert any("COSTS DISABLED" in w for w in integrity_warnings(res))

    def test_point_in_time_universe_gets_no_bias_warning(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.POINT_IN_TIME,
                  GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000))
        assert not any("SURVIVORSHIP" in w for w in integrity_warnings(res))


class TestStatsAndBenchmark:
    def test_summary_stats_are_finite_and_signed_correctly(self):
        _, data = _universe_data()
        universe = [s for s in data if s != "SPY"]
        res = run(data, universe, UniverseMode.STATIC_LIST,
                  GatedMomentumParams(n_positions=4, min_dollar_volume=1_000_000))
        assert res.total_return is not None and math.isfinite(res.total_return)
        assert res.cagr is not None and math.isfinite(res.cagr)
        assert res.max_drawdown is not None and res.max_drawdown <= 0
        assert res.annual_turnover is not None and res.annual_turnover >= 0

    def test_buy_and_hold_benchmark_matches_price_change(self):
        dates, data = _universe_data()
        bh = buy_and_hold(data, "SPY", dates[300])
        assert bh is not None
        bars = [b for b in data["SPY"] if b.t >= dates[300]]
        assert bh.total_return == pytest.approx(bars[-1].c / bars[0].c - 1)

    def test_max_drawdown_measures_the_real_trough(self):
        assert factors.max_drawdown([100, 120, 60, 90]) == pytest.approx(-0.5)

    def test_sharpe_none_on_constant_series(self):
        assert factors.annualised_sharpe([0.0] * 50) is None
