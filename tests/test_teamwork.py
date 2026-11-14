"""The teamwork panel must not peek, and the statistics must not overstate."""
from __future__ import annotations

import numpy as np
import pytest

from velocity.teamwork.panel import (RULES, latest_before, mean_window_before, trailing_rank)
from velocity.teamwork.study import cluster_stats, non_overlapping, side_of

DAY = 86_400


def test_trailing_rank_uses_only_earlier_values():
    rng = np.random.default_rng(4)
    x = rng.normal(size=900)
    r = trailing_rank(x, window=365)
    y = x.copy()
    y[500:] = 99.0                                  # rewrite the future
    assert np.allclose(r[:500], trailing_rank(y, 365)[:500], equal_nan=True)
    assert np.all(np.isnan(r[:365]))                # no rank before the window is full
    x2 = np.arange(500.0)
    assert trailing_rank(x2, 365)[-1] == pytest.approx(1.0)      # a new high ranks top
    x3 = -np.arange(500.0)
    assert trailing_rank(x3, 365)[-1] == pytest.approx(0.0)


def test_latest_before_is_strictly_before():
    t = np.array([100, 200, 300])
    v = np.array([1.0, 2.0, 3.0])
    got = latest_before(t, v, np.array([99, 100, 150, 200, 1000]))
    assert np.isnan(got[0])
    assert np.isnan(got[1])                          # a value stamped exactly at T is not yet known
    assert got[2] == 1.0 and got[3] == 1.0 and got[4] == 3.0   # 200 is also not known at 200


def test_window_mean_covers_the_right_span():
    t = np.arange(0, 10 * DAY, DAY)
    v = np.arange(10.0)
    got = mean_window_before(t, v, np.array([7 * DAY]), 7 * DAY)
    assert got[0] == pytest.approx(np.mean([1, 2, 3, 4, 5, 6, 7]))


def test_overlapping_samples_do_not_inflate_the_t_statistic():
    """Daily samples of a weekly return repeat the same information. Clustering
    by week must shrink the t-statistic by roughly sqrt(7)."""
    rng = np.random.default_rng(1)
    weekly = rng.normal(0.004, 0.02, 400)
    y = np.repeat(weekly, 7)
    week = np.repeat(np.arange(400), 7)
    clustered = cluster_stats(y, week)
    naive = y.mean() / (y.std(ddof=1) / np.sqrt(len(y)))
    assert clustered["t"] < naive / 2
    honest = cluster_stats(weekly, np.arange(400))
    assert clustered["t"] == pytest.approx(honest["t"], rel=0.05)


def test_cluster_stats_still_finds_a_real_effect():
    rng = np.random.default_rng(2)
    y = rng.normal(0.01, 0.01, 600)                  # a large, genuine mean
    s = cluster_stats(y, np.arange(600))
    assert s["t"] > 5 and s["mean_bps"] == pytest.approx(100, abs=15)


def test_events_are_counted_only_when_far_enough_apart():
    m = np.zeros(30, dtype=bool)
    m[[0, 1, 2, 10, 11, 25]] = True
    assert non_overlapping(m, 7) == 3                # 0, 10, 25
    assert non_overlapping(m, 1) == 6
    assert non_overlapping(np.zeros(30, dtype=bool), 7) == 0


def test_each_agents_condition_matches_the_plan():
    rank = {g: np.array([0.05, 0.35, 0.5, 0.65, 0.95, np.nan]) for g in RULES}
    for g, (sign, thr) in RULES.items():
        s = side_of(rank, g)
        assert s[-1] == 0.0                          # unknown reading takes no side
        for i, r in enumerate(rank[g][:-1]):
            bull = (r >= thr) if sign > 0 else (r <= thr)
            bear = ((r <= 1 - thr) if sign > 0 else (r >= 1 - thr)) and not bull
            assert s[i] == (1.0 if bull else (-1.0 if bear else 0.0))
        # bullish and bearish must never both apply to one reading
        rs = np.linspace(0, 1, 101)
        sd = side_of({g: rs}, g)
        assert set(np.unique(sd)) <= {-1.0, 0.0, 1.0}
    assert side_of({"TREND": np.array([0.9])}, "TREND")[0] == 1.0     # high trend rank is bullish
    assert side_of({"CROWD": np.array([0.9])}, "CROWD")[0] == -1.0    # crowded longs is bearish


def test_the_study_finds_an_effect_that_is_really_there():
    """A positive control: if an agent's reading genuinely predicts, the
    machinery must report it. Otherwise a null result means nothing."""
    import tempfile
    from velocity.teamwork import study as S
    rng = np.random.default_rng(7)
    n = 1200
    sig = rng.normal(size=n)
    fwd = 0.02 * np.sign(sig) + rng.normal(0, 0.005, n)      # 200 bps, in the signal's direction
    rank = (sig > 0) * 0.9 + (sig <= 0) * 0.1
    week = np.arange(n) // 7
    fake = {"days": np.arange(n) * DAY, "entry": np.ones(n), "week": week,
            "raw": {g: sig for g in RULES}, "rank": {g: rank for g in RULES},
            "fwd": {h: fwd for h in (1, 3, 7)}}
    panel = {"BTC": fake, "ETH": fake}
    with tempfile.TemporaryDirectory() as td:
        monkey = S.load
        S.load = lambda _p: panel
        try:
            res = S.run("ignored", td)
        finally:
            S.load = monkey
    assert res["verdict"] == "found"
    best = res["winners"][0]
    assert best["BTC"]["net_bps"] > 150 and best["BTC"]["t"] > 5
