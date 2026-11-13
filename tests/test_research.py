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


# ------------------------------------------------------------- three-way test

from velocity.research.three_way import (CARRY_NOTIONAL, FUT_FEE, SPOT_FEE, carry, directional,
                                         extremes_positions, funding_rank, load_funding_daily)


def test_funding_is_summed_into_the_day_it_was_paid(tmp_path):
    p = tmp_path / "f.csv"
    day = 86_400
    # three 8-hour payments on day 2, three on day 3, one stray on day 4
    p.write_text("timestamp,rate\n" + "".join(f"{t},{r}\n" for t, r in
                 [(day + 8 * 3600, 0.001), (day + 16 * 3600, 0.002), (2 * day, 0.003),
                  (2 * day + 8 * 3600, 0.01), (2 * day + 16 * 3600, 0.01), (3 * day, 0.01),
                  (3 * day + 8 * 3600, 0.5)]))
    f = load_funding_daily(str(p), [day, 2 * day, 3 * day, 4 * day, 9 * day])
    assert f[0] is None                      # before the series starts
    assert f[1] == pytest.approx(0.006)      # all three, including the one stamped at midnight
    assert f[2] == pytest.approx(0.03)
    assert f[3] is None                      # a day the series only half covers is not used
    assert f[4] is None                      # long after it ends


def test_carry_earns_funding_on_two_thirds_and_pays_to_get_in_and_out():
    closes = [100.0] * 6
    fund = [0.0, 0.001, 0.001, 0.001, 0.001, 0.001]
    r = carry(closes, fund, 0, 6, conditional=False)
    cost = CARRY_NOTIONAL * (SPOT_FEE + FUT_FEE)
    assert r[0] == pytest.approx(-cost + CARRY_NOTIONAL * 0.001)
    assert r[1] == pytest.approx(CARRY_NOTIONAL * 0.001)
    assert r[-1] == pytest.approx(CARRY_NOTIONAL * 0.001 - cost)
    # price is irrelevant to a hedged position, apart from the rebalance fee
    moved = carry([100, 100, 130, 130, 130, 130], fund, 0, 6, conditional=False)
    assert moved[1] == pytest.approx(CARRY_NOTIONAL * 0.001 - 0.15 * cost)


def test_negative_funding_costs_the_carry_and_the_conditional_version_steps_aside():
    closes = [100.0] * 30
    fund = [0.001] * 10 + [-0.002] * 20
    always = carry(closes, fund, 8, 30, conditional=False)
    cond = carry(closes, fund, 8, 30, conditional=True)
    assert sum(always) < sum(cond) < 0.01
    assert cond[-1] == 0.0                   # out of the trade by the end


def test_long_pays_funding_and_short_receives_it():
    closes = [100.0] * 4
    fund = [0.0, 0.01, 0.01, 0.01]
    long_r, _ = directional(closes, fund, [1, 1, 1, 1], 0, 4)
    short_r, _ = directional(closes, fund, [-1, -1, -1, -1], 0, 4)
    assert long_r[1] == pytest.approx(-0.01) and short_r[1] == pytest.approx(+0.01)
    assert long_r[0] == pytest.approx(-FUT_FEE - 0.01)


def test_funding_rank_uses_only_the_past():
    rng = random.Random(1)
    fund = [rng.gauss(0.0003, 0.0005) for _ in range(500)]
    t = 420
    before = funding_rank(fund, t)
    fund2 = fund[:t + 1] + [9.9] * (len(fund) - t - 1)
    assert funding_rank(fund2, t) == before
    assert funding_rank(fund, 100) is None   # not enough history yet
    hot = fund[:t - 6] + [0.01] * 7 + fund[t + 1:]
    assert funding_rank(hot, t) == 1.0       # the highest week of the year


def test_extremes_rule_fades_crowding():
    rng = random.Random(2)
    fund = [rng.gauss(0.0003, 0.0002) for _ in range(460)]
    for i in range(440, 447):
        fund[i] = 0.01                       # a week of very expensive longs
    for i in range(450, 457):
        fund[i] = -0.01                      # a week of panic
    pos = extremes_positions(fund, len(fund), 0.90, 0.10, True)
    assert pos[446] == -1 and pos[456] == 1
    assert extremes_positions(fund, len(fund), 1.01, 0.20, False)[446] == 0   # long-only never shorts
