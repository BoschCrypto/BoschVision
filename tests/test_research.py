"""The research harness must not be able to see the future or misprice costs."""
from __future__ import annotations

import random

import pytest

from velocity.research.edge_search import (FEE, FUNDING_DAY, RULES, breakout, daily_closes,
                                           ma_trend, momentum, reversal, run, stats)


def _walk(n=900, seed=3):
    rng, p, out = random.Random(seed), 100.0, []
    for _ in range(n):
        p *= 1.0 + rng.gauss(0.001, 0.03)
        out.append(p)
    return out


@pytest.mark.parametrize("name", list(RULES))
def test_no_rule_reads_the_future(name):
    """Change every price after day k: positions up to day k must not move."""
    a = _walk()
    k = 600
    b = a[:k + 1] + [x * 3.0 for x in a[k + 1:]]
    assert RULES[name](a)[:k + 1] == RULES[name](b)[:k + 1]


def test_position_earns_the_next_days_return_not_todays():
    closes = [100.0, 100.0, 200.0, 200.0]          # the jump happens on day 2
    late = run(closes, [0, 0, 1, 1], 0, 4)          # signal fires AT the jump's close
    assert sum(late["gross"]) == 0.0                # too late: earns nothing
    early = run(closes, [0, 1, 1, 1], 0, 4)
    assert early["gross"][1] == pytest.approx(1.0)


def test_costs_are_charged_on_every_change_and_funding_on_longs_only():
    closes = [100.0] * 6
    r = run(closes, [1, 1, 0, -1, -1], 0, 6)
    # day0: enter long (1 unit) + funding; day1: funding; day2: exit (1 unit);
    # day3: enter short (1 unit), no funding credit; day4: nothing
    assert r["net"][0] == pytest.approx(-FEE - FUNDING_DAY)
    assert r["net"][1] == pytest.approx(-FUNDING_DAY)
    assert r["net"][2] == pytest.approx(-FEE)
    assert r["net"][3] == pytest.approx(-FEE)
    assert r["net"][4] == pytest.approx(0.0)
    flip = run(closes, [1, -1], 0, 3)
    assert flip["net"][1] == pytest.approx(-2 * FEE)      # a flip trades two units


def test_stats_on_a_known_series():
    s = stats([0.10, -0.50, 0.10])
    assert s["total"] == pytest.approx(1.1 * 0.5 * 1.1 - 1)
    assert s["mdd"] == pytest.approx(0.5)
    assert stats([0.01] * 10)["sharpe"] == 0.0            # no variance: undefined, reported as 0


def test_rules_do_what_their_names_say():
    up = [100.0 + i for i in range(60)]
    assert momentum(up, 7, False)[-1] == 1 and momentum(up[::-1], 7, False)[-1] == 0
    assert momentum(up[::-1], 7, True)[-1] == -1
    assert ma_trend(up, 20)[-1] == 1 and ma_trend(up[::-1], 20)[-1] == 0
    assert breakout(up, 20)[-1] == 1
    crash = [100.0] * 40
    for i in range(1, 40):
        crash[i] = crash[i - 1] * (1.001 if i % 2 else 0.999)
    crash.append(crash[-1] * 0.80)
    assert reversal(crash)[-1] == 1                        # buy the day after a crash


def test_daily_close_is_the_bar_that_ends_at_midnight():
    ts = [i * 3600 for i in range(48)]
    px = [float(i) for i in range(48)]
    d_t, d_p = daily_closes(ts, px)
    assert d_t == [86_400, 172_800] and d_p == [23.0, 47.0]
