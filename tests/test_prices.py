"""Tests for the price store and the risk statistics built on it.

The store exists so that something about this allocation is measurable at n=5.
Every test here defends a way a risk number could come out wrong while looking
entirely plausible -- which is the failure mode this project keeps meeting.
"""
import csv
import math
import pathlib

import pytest
from hf_trading_bot.prices import (
    CONTINUITY_TOL, align, closes, corr, corr_se, covariance, drawdown, load,
    log_returns, portfolio_risk, vol, vol_se, weights_inverse_vol,
)

ROWS = [
    # a clean continuous series: each open equals the prior close
    ("2026-10-01", 100.0, 101.0, 99.0, 100.0, 1e6),
    ("2026-10-02", 100.0, 112.0, 99.0, 110.0, 1e6),
    ("2026-10-03", 110.0, 111.0, 98.0, 99.0, 1e6),
    ("2026-10-04", 99.0, 125.0, 98.0, 121.0, 1e6),
]


def _store(tmp_path, data: dict) -> pathlib.Path:
    d = tmp_path / "prices"
    d.mkdir()
    for asset, rows in data.items():
        with (d / f"{asset}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close", "volume_usd"])
            w.writerows(rows)
    return d


def test_a_mistyped_digit_raises_instead_of_becoming_a_return(tmp_path):
    """These rows were transcribed by hand from tool output, so a dropped or
    transposed digit is the live risk. Crypto trades through midnight, so a bar's
    open sits on the prior close; a large gap is a hole in the series or a typo
    in it, and either way the return across it never happened."""
    bad = [list(r) for r in ROWS]
    bad[2][1], bad[2][2] = 210.0, 215.0    # 110 -> 210, a transposed digit
    store = _store(tmp_path, {"X": bad})
    with pytest.raises(ValueError, match="Crypto trades through midnight"):
        load("X", store=store)


def test_the_tolerance_passes_real_microstructure(tmp_path):
    """The gap is not rounding, it is bid/ask bounce across midnight, and it
    scales with how thin the venue is -- measured at 3.3e-3 worst on NEAR_USD.
    A tolerance tight enough to reject that would reject the real data."""
    jittered = [list(r) for r in ROWS]
    jittered[1][1] = 100.0 * (1 + CONTINUITY_TOL * 0.9)
    store = _store(tmp_path, {"X": jittered})
    assert len(load("X", store=store, complete_only=False)) == 4


def test_todays_bar_is_excluded_because_it_is_still_forming(tmp_path):
    """Its close is just the last trade and its range is only the range so far.
    Mixing a six-hour return into a column of 24-hour returns drags every
    volatility toward whatever happened this morning."""
    from datetime import timezone, datetime
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [list(r) for r in ROWS] + [[today, 121.0, 130.0, 120.0, 129.0, 1e6]]
    store = _store(tmp_path, {"X": rows})
    assert [b.d.isoformat() for b in load("X", store=store)][-1] == "2026-10-04"
    assert len(load("X", store=store, complete_only=False)) == 5


def test_a_bar_whose_close_is_outside_its_range_is_refused(tmp_path):
    rows = [list(r) for r in ROWS]
    rows[0][4] = 500.0
    store = _store(tmp_path, {"X": rows})
    with pytest.raises(ValueError, match="outside the low-high range"):
        load("X", store=store)


def test_series_are_paired_by_DATE_not_by_position(tmp_path):
    """Correlating by position pairs Tuesday's BTC with Wednesday's SOL the moment
    one venue misses a bar, and the number that comes out looks fine."""
    short = [list(r) for r in ROWS if r[0] != "2026-10-02"]
    short[1][1] = short[0][4]              # keep it continuous after the removal
    store = _store(tmp_path, {"X": [list(r) for r in ROWS], "Y": short})
    dates, px = align(["X", "Y"], store=store, complete_only=False)
    assert [d.isoformat() for d in dates] == ["2026-10-01", "2026-10-03", "2026-10-04"]
    assert px["X"] == [closes("X", store=store, complete_only=False)[d] for d in dates]


def test_volatility_annualises_on_365_not_252(tmp_path):
    """Crypto trades every day. The equity convention would overstate a crypto
    volatility by sqrt(365/252) = 1.20x."""
    store = _store(tmp_path, {"X": [list(r) for r in ROWS]})
    r = log_returns([b.close for b in load("X", store=store, complete_only=False)])
    assert math.isclose(vol(r, 365) / vol(r, 252), math.sqrt(365 / 252), rel_tol=1e-12)


def test_risk_shares_sum_to_one_and_differ_from_dollar_shares(tmp_path):
    """The whole point of the decomposition. Marginal contributions sum exactly
    to the sleeve's volatility, so the shares are a decomposition rather than an
    analogy -- and on the live book BTC is 56.6% of the dollars and 28.2% of the
    risk, which a weight column cannot tell you."""
    calm = [("2026-10-01", 100.0, 100.5, 99.5, 100.0, 1e6),
            ("2026-10-02", 100.0, 100.5, 99.5, 100.5, 1e6),
            ("2026-10-03", 100.5, 101.0, 100.0, 100.0, 1e6),
            ("2026-10-04", 100.0, 100.7, 99.5, 100.6, 1e6)]
    wild = [("2026-10-01", 10.0, 13.0, 8.0, 10.0, 1e6),
            ("2026-10-02", 10.0, 14.0, 9.0, 13.0, 1e6),
            ("2026-10-03", 13.0, 14.0, 8.0, 9.0, 1e6),
            ("2026-10-04", 9.0, 15.0, 8.0, 14.0, 1e6)]
    store = _store(tmp_path, {"CALM": calm, "WILD": wild})
    R = portfolio_risk({"CALM": 0.4, "WILD": 0.4, "CASH": 0.2},
                       store=store, complete_only=False)
    assert math.isclose(sum(R["risk_share"].values()), 1.0, rel_tol=1e-12)
    assert math.isclose(sum(R["risk_contrib"].values()), R["sigma"], rel_tol=1e-12)
    assert R["weight_share"]["WILD"] == pytest.approx(0.5)
    assert R["risk_share"]["WILD"] > 0.9, "the volatile leg must dominate the risk"


def test_cash_is_carried_at_zero_volatility_and_so_cuts_sleeve_risk(tmp_path):
    """Adam's 31.8% cash slice is the single largest risk decision in his book
    and it never appears as one."""
    store = _store(tmp_path, {"X": [list(r) for r in ROWS]})
    kw = {"store": store, "complete_only": False}
    full = portfolio_risk({"X": 1.0, "CASH": 0.0}, **kw)["sigma"]
    half = portfolio_risk({"X": 0.5, "CASH": 0.5}, **kw)["sigma"]
    assert math.isclose(half, full / 2, rel_tol=1e-12)


def test_inverse_vol_weights_are_inverse_to_volatility_and_honour_the_cash_level(tmp_path):
    """Our own rule, and the point is what it does NOT contain: no fitted
    parameter, no return forecast, and no view on cash."""
    calm = [("2026-10-01", 100.0, 100.5, 99.5, 100.0, 1e6),
            ("2026-10-02", 100.0, 100.5, 99.5, 100.5, 1e6),
            ("2026-10-03", 100.5, 101.0, 100.0, 100.0, 1e6)]
    wild = [("2026-10-01", 10.0, 13.0, 8.0, 10.0, 1e6),
            ("2026-10-02", 10.0, 14.0, 9.0, 13.0, 1e6),
            ("2026-10-03", 13.0, 14.0, 8.0, 9.0, 1e6)]
    store = _store(tmp_path, {"CALM": calm, "WILD": wild})
    w = weights_inverse_vol(["CALM", "WILD"], 0.318, store=store, complete_only=False)
    assert math.isclose(sum(w.values()), 1.0, rel_tol=1e-12)
    assert math.isclose(w["CASH"], 0.318, rel_tol=1e-12)
    assert w["CALM"] > w["WILD"], "the calmer asset must get the larger weight"
    _, vols, _ = covariance(["CALM", "WILD"], store=store, complete_only=False)
    assert math.isclose(w["CALM"] / w["WILD"], vols["WILD"] / vols["CALM"], rel_tol=1e-9)


def test_an_impossible_cash_level_is_refused(tmp_path):
    store = _store(tmp_path, {"X": [list(r) for r in ROWS]})
    with pytest.raises(ValueError, match="not in"):
        weights_inverse_vol(["X"], 1.0, store=store, complete_only=False)


def test_the_estimates_report_their_own_width():
    """The claim justifying this module is that volatility is measurable at n=50
    while return is not. That claim is empty without the width behind it."""
    assert math.isclose(vol_se(0.40, 50), 0.40 / math.sqrt(100), rel_tol=1e-12)
    assert math.isclose(corr_se(0.5, 49), 0.75 / 7, rel_tol=1e-12)
    # at n=49 a correlation of 0.52 and one of 0.73 are not distinguishable
    assert corr_se(0.6, 49) > 0.09


def test_misaligned_series_cannot_be_correlated():
    with pytest.raises(ValueError, match="not aligned"):
        corr([1.0, 2.0, 3.0], [1.0, 2.0])


def test_drawdown_is_peak_to_trough_not_first_to_last():
    assert math.isclose(drawdown([100.0, 50.0, 100.0])["max"], -0.5, rel_tol=1e-12)
    assert drawdown([100.0, 110.0, 120.0])["max"] == 0.0


def test_a_second_mark_on_the_same_day_is_refused(tmp_path):
    """Two observations of one day inside a series of daily ones quietly halves
    that day's apparent return and biases every volatility built on it. Same
    failure as the duplicate signal, same refusal."""
    from hf_trading_bot.prices import marks_status, read_marks, record_marks
    f = tmp_path / "marks.jsonl"
    record_marks({"BTC": 80000.0, "ZRO": 2.0}, at="2026-10-16T00:13:24-04:00", path=f)
    record_marks({"BTC": 81000.0}, at="2026-10-17T00:10:00-04:00", path=f)
    with pytest.raises(ValueError, match="already has a mark"):
        record_marks({"BTC": 82000.0}, at="2026-10-17T20:00:00-04:00", path=f)
    assert [m for _, m in read_marks(f)["BTC"]] == [80000.0, 81000.0]
    # a different asset on that day is fine; it is the (asset, day) pair that is unique
    record_marks({"ZRO": 2.2}, at="2026-10-17T20:00:00-04:00", path=f)
    assert marks_status(f)["ZRO"]["n"] == 2


def test_a_nonpositive_mark_is_refused_before_anything_is_written(tmp_path):
    from hf_trading_bot.prices import read_marks, record_marks
    f = tmp_path / "marks.jsonl"
    with pytest.raises(ValueError, match="must be positive"):
        record_marks({"BTC": 80000.0, "ZRO": 0.0}, at="2026-10-16T00:13:24-04:00", path=f)
    assert read_marks(f) == {}, "a rejected snapshot must not half-write"


def test_the_countdown_says_how_far_off_a_volatility_is(tmp_path):
    """The gap is a countdown with a date on it, not a standing apology."""
    from hf_trading_bot.prices import marks_status, record_marks
    f = tmp_path / "marks.jsonl"
    for i in range(3):
        record_marks({"ZRO": 2.0 + i}, at=f"2026-10-{16 + i}T00:00:00-04:00", path=f)
    st = marks_status(f, need=30)["ZRO"]
    assert st["n"] == 3 and st["short_by"] == 27
    assert st["first"] == "2026-10-16" and st["last"] == "2026-10-18"


def test_a_mark_is_stamped_when_it_was_observed_not_when_the_signal_posted(tmp_path):
    """Passing the post's publication time stamped marks read at 00:13 as 20:12 the
    previous day -- four hours in their own past, and an exact duplicate of the
    next snapshot wearing a different date, which walked straight past the
    one-per-day guard. A price row's stamp is a claim about when the price
    existed."""
    from hf_trading_bot.prices import _today_et, read_marks, record_marks
    f = tmp_path / "marks.jsonl"
    record_marks({"ZRO": 2.15}, path=f)              # no `at` -> observed now
    (d,), = [[d for d, _ in v] for v in read_marks(f).values()]
    # ET, not the container's local clock. This line read `.astimezone().date()`
    # until 2026-10-20 and so asserted the very bug it now guards: on a UTC box
    # an evening ET capture lands on the next day and the assertion still passed.
    assert d == _today_et()


def test_a_day_with_no_capture_is_reported_not_silent(tmp_path):
    """The capture does not run by itself: record_marks fires inside the brief
    only when live quotes are passed, and a scheduled routine has no broker
    access, so it records nothing. Six routine runs recorded nothing between
    2026-10-14 and 2026-10-19 while the commit that added the store claimed it
    'builds itself'. The series cannot be made self-populating, so the absence
    is made loud instead."""
    from datetime import datetime, timedelta, timezone
    from hf_trading_bot.prices import marks_missing_today, record_marks
    f = tmp_path / "marks.jsonl"
    assert marks_missing_today(["BTC", "SOL"], f) == ["BTC", "SOL"], "empty store: all missing"
    record_marks({"BTC": 80000.0}, path=f)
    assert marks_missing_today(["BTC", "SOL"], f) == ["SOL"], "only the uncaptured one"
    # a capture from an earlier day does NOT count as today's
    y = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone().isoformat(timespec="seconds")
    record_marks({"SOL": 100.0}, at=y, path=f)
    assert marks_missing_today(["BTC", "SOL"], f) == ["SOL"], "yesterday is not today"


# --- the marks day boundary, found 2026-10-20 --------------------------------


def test_marks_bucket_on_the_eastern_day_not_the_utc_one(tmp_path):
    from hf_trading_bot.prices import read_marks, record_marks
    """An evening ET capture is the NEXT day in UTC, so UTC bucketing labelled
    the whole series one day forward: the row reading 2026-10-20 held the
    2026-10-19 evening mark."""
    p = tmp_path / "m.jsonl"
    record_marks({"BTC": 100.0}, at="2026-10-20T02:08:45+00:00", path=p)
    (d, _), = read_marks(p)["BTC"]
    assert d.isoformat() == "2026-10-19", "02:08 UTC is 22:08 the previous evening in ET"


def test_one_eastern_day_cannot_hold_two_captures(tmp_path):
    from hf_trading_bot.prices import read_marks, record_marks
    """The bias the mislabelling hid. A morning capture (08:47 ET = 12:47 UTC)
    and an evening one (20:12 ET = 00:12 UTC next day) fall on ONE Eastern day
    but TWO UTC days, so a single day's prices would enter a daily series as two
    observations and halve that day's apparent return."""
    p = tmp_path / "m.jsonl"
    record_marks({"BTC": 100.0}, at="2026-10-20T12:47:00+00:00", path=p)   # 08:47 ET
    with pytest.raises(ValueError, match="already has a mark"):
        record_marks({"BTC": 101.0}, at="2026-10-21T00:12:00+00:00", path=p)  # 20:12 ET same day


def test_consecutive_evenings_are_still_two_days(tmp_path):
    from hf_trading_bot.prices import read_marks, record_marks
    """The fix must not over-merge: two evening captures a day apart are two
    observations and must both be accepted."""
    p = tmp_path / "m.jsonl"
    record_marks({"BTC": 100.0}, at="2026-10-20T02:08:45+00:00", path=p)   # 10-19 ET
    record_marks({"BTC": 101.0}, at="2026-10-21T02:52:00+00:00", path=p)   # 10-20 ET
    days = [d.isoformat() for d, _ in read_marks(p)["BTC"]]
    assert days == ["2026-10-19", "2026-10-20"]


def test_the_duplicate_guard_compares_like_with_like(tmp_path):
    from hf_trading_bot.prices import read_marks, record_marks
    """The guard used to test the day in the caller's own offset against a text
    slice of the stored UTC stamp, so the same instant written two ways could
    slip past it."""
    p = tmp_path / "m.jsonl"
    record_marks({"BTC": 100.0}, at="2026-10-20T22:08:45-04:00", path=p)
    with pytest.raises(ValueError, match="already has a mark"):
        record_marks({"BTC": 101.0}, at="2026-10-21T02:08:45+00:00", path=p)
