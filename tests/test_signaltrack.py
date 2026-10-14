"""Regression tests for the signal tracker.

The point of this module is to say whether following a signal beats not following
it AFTER costs. Every test here defends a way that question could be answered
dishonestly.
"""
import json
import math
import pathlib
from dataclasses import asdict

import pytest
from hf_trading_bot.signaltrack import (
    Signal, Position, Trade, follow, hold, frozen, actual, value_position,
    apply_trades, read_trades, turnover, turnover_summary, unit_lattice,
    latest_prices, naive_weights, LEDGER,
    entry_premium, load_position, read_signals, report,
    SPREAD_PCT, LOG, POSITION)


def _sig(at, w, p):
    return Signal(at=at, weights=w, prices=p)


def _write(sigs, tmp=pathlib.Path("/tmp/claude-0/sigtest.jsonl")):
    """A throwaway log, so a test never reads or writes the real one."""
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("".join(json.dumps(asdict(s)) + "\n" for s in sigs))
    return tmp


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
    assert math.isclose(r["cost"], 500.0 * SPREAD_PCT / 2, rel_tol=1e-9)


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


# --- the real position -------------------------------------------------------
#
# These tests guard the number that every percentage in this module divides by.
# An error here does not make one line wrong; it makes all of them wrong, in a
# direction nobody notices, which is exactly how the pending-deposit error got
# through on 2026-10-07.


def _pos(**kw):
    base = dict(at="2026-10-09T22:18:02-04:00",
                units={"BTC": 0.01, "SOL": 1.0},
                basis={"BTC": 1000.0, "SOL": 100.0},
                cash=100.0)
    base.update(kw)
    return Position(**base)


def test_capital_is_what_was_paid_plus_what_was_left():
    """The denominator. Marking against invested-only would overstate every
    return by the cash weight -- 46% of this sleeve."""
    p = _pos()
    assert p.invested == 1100.0
    assert p.capital == 1200.0


def test_marking_at_the_fill_prices_shows_exactly_zero_profit():
    """The entry spread is already inside `basis`. If anything charged it again,
    a sleeve marked at the price he paid would show a phantom loss."""
    p = _pos()
    v = value_position(p, {"BTC": 100000.0, "SOL": 100.0})
    assert math.isclose(v["value"], p.capital, rel_tol=1e-12)
    assert math.isclose(v["pnl"], 0.0, abs_tol=1e-9)


def test_an_unpriceable_asset_raises_instead_of_reading_as_a_loss():
    """Silently skipping an asset with no quote would report a crash as a
    rebalancing success."""
    with pytest.raises(ValueError, match="no price for SOL"):
        value_position(_pos(), {"BTC": 100000.0})


def test_a_fill_without_a_cost_basis_is_rejected():
    """Units with no recorded basis means a fill was transcribed by hand and the
    paid amount guessed. Guessed basis is worse than no basis."""
    with pytest.raises(ValueError, match="no cost basis for SOL"):
        _pos(basis={"BTC": 1000.0}).validate()


def test_overspent_sleeve_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        _pos(cash=-1.0).validate()


def test_entry_premium_is_notional_weighted_not_a_plain_average():
    """A $5 fill at a 10% premium and a $1000 fill at 0.5% did not cost 5.25%.
    Averaging per-asset percentages would say it did, and would misprice the
    only cost that decides whether to follow this signal at all."""
    p = _pos(units={"BTC": 0.01, "SOL": 0.05},
             basis={"BTC": 1005.0, "SOL": 5.5})
    s = Signal(at="2026-10-09T20:12:00Z",
               weights={"BTC": 0.9, "SOL": 0.1},
               prices={"BTC": 100000.0, "SOL": 100.0})
    r = entry_premium(p, s)
    naive = sum(r["per_asset"].values()) / 2
    assert naive > 0.05                      # the misleading figure
    assert r["weighted"] < 0.011             # the honest one
    assert math.isclose(r["weighted"], 1010.5 / 1005.0 - 1, rel_tol=1e-9)


def test_actual_needs_a_signal_to_mark_against():
    with pytest.raises(ValueError, match="at least one signal"):
        actual(_pos(), [])


def test_report_divides_by_the_real_capital_not_a_round_number():
    """Passing a position must override a convenient start_usd. $1,000 is not
    what he committed; $1,290.84 is."""
    p = _pos()
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100000.0, "SOL": 100.0}),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100000.0, "SOL": 100.0})]
    out = report(own_weights=None, path=_write(sigs), position=p)
    assert "$1,200.00" in out
    assert "as filled, from broker records" in out


def test_report_refuses_to_invent_a_starting_capital():
    with pytest.raises(ValueError, match="either a real position or a start_usd"):
        report()


# --- the position actually on file -------------------------------------------


@pytest.mark.skipif(not POSITION.exists() or not LOG.exists(),
                    reason="live records not present")
def test_the_recorded_fills_match_the_published_signal():
    """He followed the 2026-10-09 allocation. If the recorded fills drift from
    the published weights, either the fills or the signal was mistyped, and the
    whole comparison is measuring the wrong thing."""
    pos = load_position()
    sig = read_signals()[0]
    got, want = pos.weights(), sig.weights
    assert set(got) == set(want)
    for a in want:
        assert abs(got[a] - want[a]) < 0.001, f"{a}: filled {got[a]:.4%}, signal {want[a]:.4%}"


@pytest.mark.skipif(not POSITION.exists() or not LOG.exists(),
                    reason="live records not present")
def test_the_measured_entry_cost_brackets_the_assumed_spread():
    """SPREAD_PCT is the number this entire module turns on. The real fills imply
    a round trip; if that ever lands outside half-to-double the assumption, the
    assumption is stale and every conclusion drawn from it is suspect."""
    r = entry_premium(load_position(), read_signals()[0])
    assert SPREAD_PCT / 2 < r["implied_round_trip"] < SPREAD_PCT * 2


def test_a_rotation_costs_one_round_trip_not_two():
    """Moving $100 from SOL to BTC is a sell and a buy: half a spread each, one
    SPREAD_PCT in total. Charging each leg a full spread doubled the drag and
    made the signal look twice as expensive as it is -- against the signal, and
    in favour of the argument already made for not following it."""
    p0 = {"BTC": 100.0, "SOL": 100.0}
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 0.0, "SOL": 1.0, "CASH": 0.0}, p0),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "SOL": 0.0, "CASH": 0.0}, p0)]
    r = follow(sigs, 1000.0)
    entry = 1000.0 * SPREAD_PCT / 2          # cash -> SOL
    rotation = 1000.0 * SPREAD_PCT           # SOL -> BTC, two legs
    assert math.isclose(r["cost"], entry + rotation, rel_tol=1e-9)


def test_the_strategy_and_its_benchmark_are_charged_on_the_same_scale():
    """The regression that was missed: follow() and hold() buy the identical
    thing on day one, so their entry cost must be identical. Any gap is the
    tracker tilting the comparison before a single price has moved."""
    sigs = [_sig("2026-10-01T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0}),
            _sig("2026-10-02T00:00:00Z", {"BTC": 1.0, "CASH": 0.0}, {"BTC": 100.0})]
    assert math.isclose(follow(sigs, 1000.0)["cost"],
                        hold("BTC", sigs, 1000.0)["cost"], rel_tol=1e-9)


# --- the ledger --------------------------------------------------------------


def test_the_denominator_survives_a_realised_loss():
    """Capital must stay pinned to what he put in. Recomputing it as
    paid-plus-cash after a losing sale shrinks the denominator by the amount
    lost, which reports a smaller loss than actually happened."""
    live = Position(at="2026-10-11T09:58:46-04:00",
                    units={"BTC": 0.01}, basis={"BTC": 900.0}, cash=99.0,
                    committed=1000.0, realized=-1.0)
    assert live.capital == 1000.0            # not 999.0
    v = value_position(live, {"BTC": 90000.0})
    assert math.isclose(v["pnl"], -1.0, abs_tol=1e-9)


def test_replaying_the_ledger_cannot_sell_units_that_are_not_there():
    """A fat-fingered quantity would otherwise produce a negative holding and a
    sleeve value that looks fine."""
    pos = _pos()
    t = Trade(at="2026-10-11T10:00:00-04:00", asset="SOL", side="sell",
              units=99.0, notional=10.0, basis_delta=-10.0)
    with pytest.raises(ValueError, match="sold below zero"):
        apply_trades(pos, [t])


def test_a_sale_realises_proceeds_less_disposed_basis():
    t = Trade(at="2026-10-11T09:58:33-04:00", asset="SOL", side="sell",
              units=0.2606, notional=28.22, basis_delta=-28.88)
    assert math.isclose(t.realized, -0.66, abs_tol=1e-9)
    assert Trade(at="x", asset="BTC", side="buy", units=1.0,
                 notional=10.0, basis_delta=10.0).realized == 0.0


def test_a_buy_that_removes_basis_is_rejected():
    with pytest.raises(ValueError, match="a buy adds cost basis"):
        Trade(at="x", asset="BTC", side="buy", units=1.0,
              notional=10.0, basis_delta=-10.0).validate()


@pytest.mark.skipif(not POSITION.exists() or not LEDGER.exists(),
                    reason="live records not present")
def test_the_replayed_ledger_matches_the_brokers_own_records():
    """The whole tracker rests on its baseline matching the account. Broker
    figures read back after the fills on 2026-10-12."""
    live = apply_trades(load_position(), read_trades())
    for a, (u, b) in {"BTC": (0.00593502, 494.33), "SUI": (213.682, 234.95),
                      "NEAR": (29.14, 147.08)}.items():
        assert abs(live.units[a] - u) < 1e-8, f"{a} units"
        assert abs(live.basis[a] - b) < 0.005, f"{a} basis"
    assert "SOL" not in live.units, "SOL was fully exited 2026-10-12"
    assert abs(live.cash - 408.98) < 0.005
    assert abs(live.capital - 1290.84) < 0.005   # float sum of the four fills


def test_a_fully_exited_asset_leaves_the_sleeve():
    """A zero-unit entry would force every later report to carry a price for
    something no longer held — the day that coin stops being quoted, the whole
    report raises instead of printing."""
    pos = _pos()
    live = apply_trades(pos, [Trade(at="2026-10-12T20:54:46-04:00", asset="SOL",
                                    side="sell", units=1.0, notional=95.0,
                                    basis_delta=-100.0)])
    assert "SOL" not in live.units and "SOL" not in live.basis
    assert math.isclose(live.realized, -5.0, abs_tol=1e-9)
    value_position(live, {"BTC": 100000.0})      # no SOL price needed, must not raise


def test_basis_left_on_a_fully_sold_asset_is_refused():
    """Units at zero with basis remaining means the disposal was mis-recorded.
    Carrying it would overstate the sleeve by that amount forever."""
    with pytest.raises(ValueError, match="fully sold but"):
        apply_trades(_pos(), [Trade(at="x", asset="SOL", side="sell", units=1.0,
                                    notional=95.0, basis_delta=-40.0)])


# --- what the weights themselves reveal --------------------------------------


def test_the_unit_lattice_is_falsifiable():
    """The claim is that every published weight is an exact k/44. A check that
    cannot fail is not evidence, so this pins that an off-lattice weight is
    actually rejected."""
    on = _sig("2026-10-09T20:12:00Z",
              {"BTC": 14/44, "SUI": 8/44, "NEAR": 5/44, "SOL": 3/44, "CASH": 14/44},
              {"BTC": 1, "SUI": 1, "NEAR": 1, "SOL": 1})
    assert unit_lattice([on])["holds"]
    off = _sig("2026-10-10T20:12:00Z",
               {"BTC": 0.20, "SUI": 0.20, "NEAR": 0.20, "SOL": 0.20, "CASH": 0.20},
               {"BTC": 1, "SUI": 1, "NEAR": 1, "SOL": 1})
    assert not unit_lattice([off])["holds"], "20% is not a multiple of 1/44"


def test_turnover_is_one_way_not_double_counted():
    """Rotating 10% of the sleeve from SOL to BTC moves 10%, not 20%. Summing
    absolute changes without halving would double every cost this produces."""
    p = {"BTC": 1.0, "SOL": 1.0}
    r = turnover([_sig("2026-10-09T00:00:00Z", {"BTC": 0.4, "SOL": 0.2, "CASH": 0.4}, p),
                  _sig("2026-10-10T00:00:00Z", {"BTC": 0.5, "SOL": 0.1, "CASH": 0.4}, p)])
    assert math.isclose(r[0]["turnover"], 0.10, rel_tol=1e-9)
    assert math.isclose(r[0]["cost"], 0.10 * SPREAD_PCT, rel_tol=1e-9)


def test_an_unchanged_signal_generates_no_turnover():
    w = {"BTC": 0.5, "CASH": 0.5}
    r = turnover([_sig("2026-10-10T00:00:00Z", w, {"BTC": 1.0}),
                  _sig("2026-10-11T00:00:00Z", w, {"BTC": 1.0})])
    assert r[0]["turnover"] == 0.0 and r[0]["moved"] == {}


def test_the_turnover_interval_is_reported_and_is_wide_at_small_n():
    """A point estimate off three transitions is not a measurement. If this ever
    returns a mean without an interval, the number will get quoted as fact."""
    p = {"BTC": 1.0, "SOL": 1.0}
    # turnover of 2%, then 0%, then 6% -- real signals vary, and it is that
    # variance the interval is meant to expose
    btc = [0.40, 0.42, 0.42, 0.48]
    sigs = [_sig(f"2026-10-0{i+1}T00:00:00Z",
                 {"BTC": b, "SOL": 0.60 - b, "CASH": 0.0}, p)
            for i, b in enumerate(btc)]
    s = turnover_summary(sigs)
    assert s["n"] == 3 and math.isclose(s["no_change_rate"], 1/3, rel_tol=1e-9)
    assert s["ci"][1] > s["mean"] > s["ci"][0], "a varying sample must yield a real interval"
    assert s["ci"][0] == 0.0, "turnover cannot be negative; the bound is clipped at zero"
    assert turnover_summary(sigs[:1])["n"] == 0


def test_a_dropped_asset_is_still_priced_for_the_control_arm():
    """The control arm keeps holding what it bought. When a signal drops an asset
    the newest price row no longer covers the frozen sleeve, and marking it
    raised instead of printing -- the exact failure this guards."""
    p = {"BTC": 100.0, "SOL": 50.0}
    sigs = [_sig("2026-10-09T00:00:00Z", {"BTC": 0.5, "SOL": 0.5}, p),
            _sig("2026-10-13T00:00:00Z", {"BTC": 1.0}, {"BTC": 120.0})]
    px, as_of = latest_prices(sigs)
    assert px == {"BTC": 120.0, "SOL": 50.0}
    assert as_of["BTC"] == sigs[-1].at and as_of["SOL"] == sigs[0].at
    value_position(_pos(), px)          # must not raise


def test_a_live_quote_overrides_the_carried_forward_price():
    """A price carried forward from an older row is stale by construction. When a
    live quote is to hand it must win, and be labelled as live."""
    sigs = [_sig("2026-10-09T00:00:00Z", {"BTC": 0.5, "SOL": 0.5},
                 {"BTC": 100.0, "SOL": 50.0}),
            _sig("2026-10-13T00:00:00Z", {"BTC": 1.0}, {"BTC": 120.0})]
    px, as_of = latest_prices(sigs, {"SOL": 107.68})
    assert px["SOL"] == 107.68 and as_of["SOL"] == "live"


def test_the_naive_benchmark_needs_no_foresight():
    """Equal weight across whatever the FIRST signal named, at its cash level.
    Anything derived from which legs later turned out to be static would be
    hindsight dressed as a benchmark."""
    sigs = [_sig("2026-10-09T00:00:00Z",
                 {"BTC": 0.50, "SUI": 0.12, "SOL": 0.06, "CASH": 0.32},
                 {"BTC": 1.0, "SUI": 1.0, "SOL": 1.0}),
            _sig("2026-10-13T00:00:00Z", {"BTC": 0.68, "CASH": 0.32}, {"BTC": 1.0})]
    w = naive_weights(sigs)
    assert w["CASH"] == 0.32
    assert math.isclose(w["BTC"], w["SUI"]) and math.isclose(w["BTC"], w["SOL"])
    assert math.isclose(sum(w.values()), 1.0, rel_tol=1e-9)
    assert "SOL" in w, "an asset the signal later dropped still belongs on day one"


def test_a_frozen_sleeve_never_silently_drops_an_unpriceable_leg():
    """_value() skips assets it cannot price. Marking a frozen sleeve on the
    latest signal's prices after a drop would therefore under-report it by that
    holding's whole value, with no error — the quiet failure this forbids."""
    sigs = [_sig("2026-10-09T00:00:00Z", {"BTC": 0.5, "SOL": 0.5},
                 {"BTC": 100.0, "SOL": 50.0}),
            _sig("2026-10-13T00:00:00Z", {"BTC": 1.0}, {"BTC": 100.0})]
    with pytest.raises(ValueError, match="cannot mark the frozen sleeve"):
        frozen({"BTC": 0.5, "SOL": 0.5}, sigs, 1000.0, prices={"BTC": 100.0})
    r = frozen({"BTC": 0.5, "SOL": 0.5}, sigs, 1000.0)   # carries SOL forward
    assert math.isclose(r["value"], 1000.0 - 1000.0 * SPREAD_PCT / 2, rel_tol=1e-9)
