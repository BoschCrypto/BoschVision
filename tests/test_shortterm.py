"""The short-term backtester must not see the future, must fill pessimistically,
and must keep one honest balance."""
from __future__ import annotations

import numpy as np
import pytest

from velocity.shortterm import features, strategies
from velocity.shortterm.config import PERP, SPOT, RiskBudget
from velocity.shortterm.data import resample
from velocity.shortterm.sim import Market, TradeState, portfolio, resolve, size_position, stats


def tape(n=20_000, seed=1, start=100.0, vol=0.0006):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate(([start], c[:-1]))
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    return {"ts": 1_700_000_000 // 3600 * 3600 + 60 * np.arange(n, dtype=np.int64), "open": o, "high": hi,
            "low": lo, "close": c, "volume": np.ones(n), "gap": np.zeros(n, dtype=bool)}


def flat_tape(prices, t0=1_700_006_400):
    """One-minute bars where open=high=low=close unless given as (o,h,l,c)."""
    rows = [(p, p, p, p) if not isinstance(p, tuple) else p for p in prices]
    a = np.array(rows, dtype=float)
    n = len(a)
    return {"ts": t0 + 60 * np.arange(n, dtype=np.int64), "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
            "close": a[:, 3], "volume": np.ones(n), "gap": np.zeros(n, dtype=bool)}


def mk_for(m1, inst=PERP["BTC"]):
    f = {"is5end": np.zeros(len(m1["ts"]), dtype=bool), "k_of_min": np.zeros(len(m1["ts"]), dtype=int),
         "atr": np.array([1.0])}
    return Market("BTC", m1, f, inst)


def S(i, side=1, stop=None, **kw):
    d = {"asset": "BTC", "cfg": "t", "i": i, "side": side, "entry": "market", "stop": stop, "r_mult": None,
         "trail": None, "time_min": None, "exit_i": None, "reject": "", "uid": f"t{i}{side}{stop}{kw}"}
    d.update(kw)
    return d


# ----------------------------------------------------------------- causality

def test_resample_only_returns_complete_bars_and_says_when_they_closed():
    m1 = tape(1_000)
    b = resample(m1, 5)
    k = 7
    sl = slice(b["end_i"][k] - 4, b["end_i"][k] + 1)
    assert b["high"][k] == m1["high"][sl].max() and b["close"][k] == m1["close"][b["end_i"][k]]
    assert b["end_i"][-1] <= len(m1["ts"]) - 1
    assert ((m1["ts"][b["end_i"]] + 60) % 300 == 0).all()    # each bar ends on a 5-minute boundary


@pytest.mark.parametrize("cfg", strategies.registry(), ids=lambda c: c["id"])
def test_no_strategy_reads_the_future(cfg):
    """Rewrite every price after minute K. Setups known by K must not change."""
    a = tape(20_000, seed=3)
    K = 15_000
    b = {k: v.copy() for k, v in a.items()}
    for k in ("open", "high", "low", "close"):
        b[k][K + 1:] = b[k][K + 1:] * 1.5
    sa = strategies.setups_for(a, features.build(a), cfg, "BTC")
    sb = strategies.setups_for(b, features.build(b), cfg, "BTC")
    core = lambda xs: [(s["i"], s["side"], s["entry"], s["stop"], s["reject"]) for s in xs if s["i"] <= K]
    assert core(sa) == core(sb)
    assert sa, "strategy produced no setups on the test tape"


def test_features_match_when_recomputed_on_a_trailing_window():
    """Paper trading recomputes on recent bars only. Finite windows mean the
    numbers must equal the full-history ones."""
    a = tape(40_000, seed=5)
    full = features.build(a)
    cut = 40_000 - 28_800
    w = {k: v[cut:] for k, v in a.items()}
    part = features.build(w)
    for key in ("atr", "hz24", "hsma72", "range_med7d", "lvl_hi12", "hh36"):
        x, y = full[key][-50:], part[key][-50:]
        assert np.allclose(x, y, equal_nan=True), key


# --------------------------------------------------------------------- fills

def test_market_order_fills_at_the_next_minutes_open_not_the_signal_close():
    m1 = flat_tape([100, 100, (105, 106, 104, 105), 105, 105, 105])
    r = resolve(mk_for(m1), S(1, time_min=2))
    assert r["entry_i"] == 2 and r["entry"] == 105.0


def test_stop_and_target_in_the_same_bar_counts_as_the_stop():
    m1 = flat_tape([100, 100, 100, (100, 103, 97, 100), 100, 100])
    r = resolve(mk_for(m1), S(1, stop=98.0, r_mult=1.0))          # target 102, both inside bar 3
    assert r["how"] == "stop" and r["exit"] == 98.0


def test_a_touch_does_not_fill_a_limit_but_trading_through_does():
    inst = PERP["BTC"]
    touch = flat_tape([100, 100, 100, (100, 102.0, 100, 100), 100, 100, 100, 100])
    r = resolve(mk_for(touch), S(1, stop=98.0, r_mult=1.0, time_min=4))
    assert r["how"] == "time limit"
    thru = flat_tape([100, 100, 100, (100, 102.0 * (1 + 2 * inst.through), 100, 100), 100, 100])
    r2 = resolve(mk_for(thru), S(1, stop=98.0, r_mult=1.0, time_min=4))
    assert r2["how"] == "target" and r2["exit"] == pytest.approx(102.0)


def test_a_gapped_stop_fills_at_the_open():
    m1 = flat_tape([100, 100, 100, 100, (95, 95, 94, 94), 94])
    r = resolve(mk_for(m1), S(1, stop=98.0))
    assert r["how"] == "stop (gapped)" and r["exit"] == 95.0


def test_costs_are_charged_both_ways_and_limits_pay_the_maker_fee():
    inst = PERP["ETH"]
    m1 = flat_tape([100] * 8)
    r = resolve(mk_for(m1, inst), S(1, time_min=3))
    assert r["gross"] == 0.0
    assert r["cost"] == pytest.approx(2 * (10 + 5.16 / 2 + 0.5) / 1e4)
    assert r["net"] < 0
    win = flat_tape([100, 100, 100, (100, 103, 100, 103), 103, 103])
    r2 = resolve(mk_for(win, inst), S(1, stop=98.0, r_mult=1.0))
    assert r2["cost"] == pytest.approx((10 + 5.16 / 2 + 0.5) / 1e4 + 9.5 / 1e4)


def test_stop_entry_needs_the_price_and_expires_without_it():
    m1 = flat_tape([100, 100, 100, 100, (100, 101.5, 100, 101.2), 101.2, 101.2, 101.2])
    order = S(1, entry="stop", legs=[(1, 101.0, 99.0), (-1, 97.0, 99.0)], expiry_i=6, stop=99.0, time_min=2)
    r = resolve(mk_for(m1), order)
    assert r["filled"] and r["side"] == 1 and r["entry"] == 101.0 and r["entry_i"] == 4
    quiet = flat_tape([100] * 10)
    r2 = resolve(mk_for(quiet), S(1, entry="stop", legs=[(1, 101.0, 99.0)], expiry_i=6, stop=99.0))
    assert not r2["filled"] and "never reached" in r2["why"]


def test_a_stop_entry_filled_in_a_bar_that_also_hits_its_stop_is_a_loss():
    m1 = flat_tape([100, 100, 100, (100, 101.5, 98.5, 100), 100, 100])
    r = resolve(mk_for(m1), S(1, entry="stop", legs=[(1, 101.0, 99.0)], expiry_i=5, stop=99.0))
    assert r["how"] == "stop" and r["net"] < 0


def test_spot_cannot_short_and_pays_much_more():
    m1 = flat_tape([100] * 8)
    assert not resolve(mk_for(m1, SPOT["BTC"]), S(1, side=-1, time_min=2))["filled"]
    assert resolve(mk_for(m1, SPOT["BTC"]), S(1, time_min=2))["cost"] > 0.017


def test_trailing_stop_only_ratchets_in_the_trades_favour():
    st = TradeState(PERP["BTC"], 1, 100.0, 98.0, None, 2.0, None, None)
    assert st.on_bar(0, 100, 105, 100, True, 1.0) is None
    assert st.stop == pytest.approx(103.0)
    assert st.on_bar(1, 104, 104.5, 103.5, True, 5.0) is None       # wider ATR must not loosen it
    assert st.stop == pytest.approx(103.0)
    assert st.on_bar(2, 104, 104, 102.9, False, 1.0)[1] == "stop"


def test_a_trade_cannot_cross_a_window_boundary():
    m1 = flat_tape([100] * 30)
    r = resolve(mk_for(m1), S(1), end_i=10)
    assert r["how"] == "window end" and r["exit_i"] == 10
    assert not resolve(mk_for(m1), S(9), end_i=10)["filled"]


# ------------------------------------------------------------------- account

def test_warden_sizes_by_risk_then_caps_then_whole_contracts():
    b, inst = RiskBudget(), PERP["ETH"]
    units, notional, why = size_position(b, inst, 5_000, 2_000.0, 0.01)
    assert notional == pytest.approx(1_200.0) and why == "position limit"      # 6 contracts of $200
    units, notional, why = size_position(b, inst, 5_000, 2_000.0, 0.015, other_notional=1_250.0, other_risk=12.5)
    assert notional == pytest.approx(600.0) and "shared" in why
    assert size_position(b, PERP["BTC"], 2_000, 70_000.0, 0.01)[0] == 0.0     # one contract is too big
    frac = size_position(b, SPOT["BTC"], 5_000, 70_000.0, 0.02)
    assert frac[1] == pytest.approx(1_250.0)


def _two_markets(n=6_000):
    out = {}
    for a, seed in (("BTC", 11), ("ETH", 12)):
        m1 = tape(n, seed=seed, start=70_000.0 if a == "BTC" else 2_000.0)
        out[a] = (m1, features.build(m1))
    return out


def test_account_balance_is_the_sum_of_its_trades_and_shares_one_budget():
    data = _two_markets()
    cfg = next(c for c in strategies.registry() if c["id"] == "S1-T24-ls")
    markets = {a: Market(a, m1, f, PERP[a]) for a, (m1, f) in data.items()}
    setups = {a: strategies.setups_for(m1, f, cfg, a) for a, (m1, f) in data.items()}
    n = len(data["BTC"][0]["ts"])
    res = portfolio(markets, setups, RiskBudget(), 3_000, n - 1)
    assert res.trades
    assert res.equity[-1][1] == pytest.approx(5_000 + sum(t["pnl"] for t in res.trades))
    st = stats(res)
    assert st["ALL"]["trades"] == st["BTC"]["trades"] + st["ETH"]["trades"]
    assert all(t["exit_i"] <= n - 1 and t["entry_i"] >= 3_000 for t in res.trades)
    # never more than the shared position limit open at once
    for t in res.trades:
        others = [u for u in res.trades if u is not t and u["entry_i"] < t["exit_i"] and t["entry_i"] < u["exit_i"]]
        assert t["notional"] + max([u["notional"] for u in others], default=0) <= 0.375 * 5_300


def test_one_position_per_asset_and_skips_are_logged_with_a_reason():
    data = _two_markets()
    cfg = next(c for c in strategies.registry() if c["id"] == "F3-N4-any-R1.0")
    markets = {a: Market(a, m1, f, PERP[a]) for a, (m1, f) in data.items()}
    setups = {a: strategies.setups_for(m1, f, cfg, a) for a, (m1, f) in data.items()}
    n = len(data["BTC"][0]["ts"])
    res = portfolio(markets, setups, RiskBudget(), 3_000, n - 1)
    for a in ("BTC", "ETH"):
        tr = sorted((t for t in res.trades if t["asset"] == a), key=lambda t: t["entry_i"])
        assert all(x["exit_i"] <= y["entry_i"] for x, y in zip(tr, tr[1:]))
    taken = len(res.trades)
    eligible = sum(1 for a in setups for s in setups[a] if 3_000 <= s["i"] < n - 1)
    assert taken + len(res.skipped) == eligible
    assert all(s["why"] for s in res.skipped)


def test_daily_loss_limit_stops_new_entries_until_the_next_day():
    # a tape that falls steadily: every long loses
    n = 3_000
    px = 2_000.0 * np.exp(-0.0004 * np.arange(n))
    m1 = flat_tape(list(px), t0=1_700_006_400)
    f = features.build(m1)
    mk = {"ETH": Market("ETH", m1, f, PERP["ETH"])}
    longs = [S(i, stop=float(px[i] * 0.99), time_min=30, asset="ETH") for i in range(100, n - 200, 40)]
    for k, s in enumerate(longs):
        s["uid"] = f"x{k}"
    res = portfolio(mk, {"ETH": longs}, RiskBudget(), 50, n - 1)
    reasons = {s["why"] for s in res.skipped}
    assert "daily loss limit reached" in reasons
    by_day = {}
    for t in res.trades:
        by_day.setdefault(t["exit_ts"] // 86_400, []).append(t["pnl"])
    start = 5_000.0
    for d in sorted(by_day):
        day_pnl = sum(by_day[d])
        assert day_pnl >= -0.02 * start - 0.0045 * start       # limit, plus at most one trade's overshoot
        start += day_pnl


def test_a_rule_that_flips_sides_trades_both_sides():
    """The short after a long used to be refused as 'already in a position'
    because the long's exit and the short's entry are known the same minute."""
    data = _two_markets(12_000)
    cfg = next(c for c in strategies.registry() if c["id"] == "S1-T24-ls")
    m1, f = data["ETH"]
    mk = {"ETH": Market("ETH", m1, f, PERP["ETH"])}
    setups = {"ETH": strategies.setups_for(m1, f, cfg, "ETH")}
    res = portfolio(mk, setups, RiskBudget(), 3_000, len(m1["ts"]) - 1)
    sides = [t["side"] for t in sorted(res.trades, key=lambda t: t["entry_i"])]
    assert 1 in sides and -1 in sides
    flips = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
    assert flips >= 0.6 * (len(sides) - 1)


def test_positive_control_a_rule_that_sees_the_future_makes_money():
    """If the simulator could not show a profit for a cheat, a loss for an
    honest rule would mean nothing."""
    m1 = tape(12_000, seed=21, start=2_000.0, vol=0.001)
    f = features.build(m1)
    c = m1["close"]
    cheats = []
    for i in range(3_000, 11_000, 120):
        fut = c[i + 90] / c[i] - 1
        side = 1 if fut > 0 else -1
        cheats.append(S(i, side=side, stop=float(c[i] * (1 - side * 0.012)), time_min=90, asset="ETH"))
        cheats[-1]["uid"] = f"cheat{i}"
    res = portfolio({"ETH": Market("ETH", m1, f, PERP["ETH"])}, {"ETH": cheats}, RiskBudget(), 2_900, 11_500)
    st = stats(res)
    assert st["ALL"]["avg_net_bps"] > 20 and st["ALL"]["net_usd"] > 0 and st["ALL"]["win_rate"] > 0.6
