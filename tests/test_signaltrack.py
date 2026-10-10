"""Regression tests for the signal tracker.

The point of this module is to say whether following a signal beats not following
it AFTER costs. Every test here defends a way that question could be answered
dishonestly.
"""
import math
import pytest
from hf_trading_bot.signaltrack import (
    Signal, follow, hold, frozen, SPREAD_PCT, MIN_TRADE_USD)


def _sig(at, w, p):
    return Signal(at=at, weights=w, prices=p)


def test_weights_must_include_cash_and_sum_to_one():
    """Adam's posts name cash as a slice. A set that sums to 0.68 means the cash
    line was dropped, which would silently inflate every crypto weight."""
    s = _sig("2026-10-09T20:12:00Z", {"BTC": 0.318, "SOL": 0.068}, {"BTC": 1, "SOL": 1})
    with pytest.raises(ValueError, match="not 1.0"):
        s.validate()


def test_an_unchanged_signal_costs_nothing():
    """Re-publishing the same allocation must not generate turnover. If it does,
    the tracker manufactures cost the follower never paid."""
    w = {"BTC": 0.5, "CASH": 0.5}
    p = {"BTC": 80000.0}
    r = follow([_sig("2026-10-01T00:00:00Z", w, p),
                _sig("2026-10-02T00:00:00Z", w, p)], 1000.0)
    assert r["trades"] == 1          # the initial purchase only
    assert math.isclose(r["cost"], 500.0 * SPREAD_PCT, rel_tol=1e-9)


def test_drift_under_the_minimum_is_not_traded():
    """A $3 correction costs more in spread than it fixes. The tracker must model
    the same restraint a sane follower would show."""
    p = {"BTC": 80000.0}
    r = follow([_sig("2026-10-01T00:00:00Z", {"BTC": 0.50, "CASH": 0.50}, p),
                _sig("2026-10-02T00:00:00Z", {"BTC": 0.505, "CASH": 0.495}, p)], 1000.0)
    assert r["trades"] == 1          # the 0.5pp move is $5, under MIN_TRADE_USD


def test_spread_is_charged_and_reduces_the_result():
    """A tracker that ignored the spread would be the whole point missed."""
    p0, p1 = {"BTC": 80000.0}, {"BTC": 80000.0}
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, p0),
            _sig("2026-10-02T00:00:00Z", {"BTC": 0.0, "CASH": 1.0}, p1)]
    r = follow(sigs, 1000.0)
    assert r["cost"] > 0
    assert r["value"] < 1000.0, "flat prices + two full rotations must LOSE money"


def test_perfect_timing_still_pays_the_toll():
    """Even a signal that calls every move correctly hands back SPREAD_PCT of the
    notional it moves. This is the arithmetic that decides whether to subscribe."""
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0}),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 200.0})]
    r = follow(sigs, 1000.0)
    gross = 1000.0 * 2
    assert r["value"] < gross
    assert math.isclose(gross - r["value"], r["cost"], rel_tol=1e-6)


def test_hold_benchmark_pays_entry_only():
    """Buy-and-hold crosses the spread once. Charging it twice would flatter the
    signal by making its benchmark look artificially weak."""
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0}),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0})]
    b = hold("BTC", sigs, 1000.0)
    assert math.isclose(b["cost"], 1000.0 * SPREAD_PCT / 2, rel_tol=1e-9)
    assert math.isclose(b["value"], 1000.0 - b["cost"], rel_tol=1e-9)


def test_frozen_benchmark_never_trades_after_entry():
    """The frozen sleeve isolates the signal's value from the assets' value."""
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0}),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 150.0})]
    z = frozen({"BTC": 0.5, "CASH": 0.5}, sigs, 1000.0)
    # 500 of BTC rises 50% -> 750, plus 500 cash less the one-way entry cost
    expected = 750.0 + 500.0 - 1000.0 * 0.5 * SPREAD_PCT / 2
    assert math.isclose(z["value"], expected, rel_tol=1e-9)
