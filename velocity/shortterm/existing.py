"""The system as it runs today, measured two ways.

    python -m velocity.shortterm.existing baseline DATA_DIR OUT_DIR research|final
    python -m velocity.shortterm.existing arbiter  DATA_DIR OUT_DIR

`baseline` replays the current engine (velocity.engine, presets.perp_engine)
on one-minute bars for BTC and ETH with ONE shared balance, three times:
the five agents as configured, QUANT alone, CHARTIST alone. Fees and spread
are the same measured figures the new backtester uses. Two differences from
the new backtester, both inherent to the existing engine and both small:
its orders fill at the deciding bar's close plus its own latency model
rather than at the next bar's open, and its daily loss limit is per asset.

`arbiter` runs the same agents in shadow (no trading) over the research
period and asks whether the +/-0.28 threshold, CHARTIST's vote, and either
agent's confidence carry information (PLAN.md section 6).
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

from ..agents.arbiter import Arbiter
from ..execution import ExecConfig
from ..presets import perp_engine
from ..types import NS_PER_S, Tick
from .config import PERP, RiskBudget
from .run import ASSETS, DAY, FINAL_START, TRAIN_DAYS, WARM_DAYS, day_str, load_m1

HORIZON = {"BTC": 144, "ETH": 78}
VARIANTS = {"current": None, "quant_only": {"QUANT": 1.0}, "chartist_only": {"CHARTIST": 1.0}}
WARM_BARS = 1800


def make_engine(asset: str, weights=None):
    inst = PERP[asset]
    eng = perp_engine(asset, equity=RiskBudget().equity, contract_size=inst.contract,
                      horizon_min=HORIZON[asset], fee_bps=inst.taker_bps)
    eng.executor.cfg = ExecConfig(latency_ms=100.0, slippage_bps=inst.slip_bps, fee_bps=inst.taker_bps,
                                  assume_spread_bps=inst.spread_bps, seed=4)
    if weights is not None:
        eng.arbiter = Arbiter(weights=weights, entry_threshold=0.28, exit_threshold=0.10, min_agreeing=1,
                              min_hold_s=3600.0, exit_persist=3)
        eng.fast[-1] = eng.arbiter
    return eng


def baseline(data_dir: str, out_dir: str, window: str) -> dict:
    upto = FINAL_START if window == "research" else None
    m1 = load_m1(data_dir, upto)
    ts = m1["BTC"]["ts"]
    if window == "research":
        i0 = (WARM_DAYS + TRAIN_DAYS) * DAY          # where the first walk-forward test fold begins
    else:
        i0 = int(np.searchsorted(ts, FINAL_START))
    i1 = len(ts)
    start = RiskBudget().equity
    out = {"window": f"{day_str(ts[i0])} to {day_str(ts[i1 - 1])}"}
    for name, weights in VARIANTS.items():
        t0 = time.time()
        eng = {a: make_engine(a, weights) for a in ASSETS}
        c = {a: m1[a]["close"] for a in ASSETS}
        v = {a: m1[a]["volume"] for a in ASSETS}
        for a in ASSETS:
            eng[a].bb.halt("warm-up")
        curve, peak, mdd, halted_on = [], start, 0.0, None
        in_mkt = {a: 0 for a in ASSETS}
        for i in range(i0 - WARM_BARS, i1):
            if i == i0:
                for a in ASSETS:
                    eng[a].bb.halted, eng[a].bb.halt_reason = False, ""
                    eng[a].vetoes.clear()
            t_ns = (int(ts[i]) + 60) * NS_PER_S
            for a in ASSETS:
                eng[a].on_tick(Tick(t_ns, a, float(c[a][i]), float(v[a][i])))
            if i < i0:
                continue
            shared = start + sum(eng[a].bb.realised_pnl for a in ASSETS)
            for a in ASSETS:                         # one balance for both
                eng[a].bb.equity = shared
                in_mkt[a] += 1 if eng[a].bb.open_side else 0
            if i % 60 == 0:
                mark = shared + sum(eng[a].bb.unrealised(float(c[a][i])) for a in ASSETS)
                peak = max(peak, mark)
                mdd = max(mdd, 1 - mark / peak)
                curve.append((int(ts[i]), round(mark, 2)))
                if halted_on is None and all(eng[a].bb.drawdown >= 0.08 for a in ASSETS):
                    halted_on = day_str(ts[i])
        for a in ASSETS:
            if not eng[a].bb.flat:
                eng[a].executor.close(Tick((int(ts[i1 - 1]) + 60) * NS_PER_S, a, float(c[a][i1 - 1])),
                                      eng[a].bb, "window end", 0.0)
        res = {}
        for a in list(ASSETS) + ["ALL"]:
            tr = [t for x in ASSETS if a in (x, "ALL") for t in eng[x].executor.trades]
            pnl = [t.pnl for t in tr]
            bps = [t.pnl / (t.qty * t.entry) * 1e4 for t in tr if t.qty * t.entry > 0]
            wins, losses = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
            res[a] = {"trades": len(tr), "net_usd": round(sum(pnl), 2), "net_pct": round(sum(pnl) / start * 100, 3),
                      "avg_net_bps": round(float(np.mean(bps)), 2) if bps else 0.0,
                      "win_rate": round(sum(1 for p in pnl if p > 0) / len(pnl), 3) if pnl else 0.0,
                      "profit_factor": round(wins / losses, 3) if losses > 0 else 0.0,
                      "costs_usd": round(sum(t.costs for t in tr), 2),
                      "time_in_market": round((in_mkt[a] if a != "ALL" else max(in_mkt.values())) / (i1 - i0), 3)}
        res["ALL"]["max_drawdown_pct"] = round(mdd * 100, 2)
        res["ALL"]["drawdown_brake_hit"] = halted_on
        res["ALL"]["blocked"] = {a: dict(sorted(eng[a].vetoes.items(), key=lambda kv: -kv[1])[:4]) for a in ASSETS}
        out[name] = res
        with open(os.path.join(out_dir, f"baseline_{window}_{name}_equity.json"), "w") as fh:
            json.dump(curve, fh)
        r = res["ALL"]
        print(f"{name:<14} {out['window']}  trades {r['trades']:>5}  net {r['net_pct']:>+7.2f}%  maxDD {r['max_drawdown_pct']:.2f}%  "
              f"avg {r['avg_net_bps']:>+6.1f} bps  PF {r['profit_factor']:.2f}  costs ${r['costs_usd']:,.0f}  "
              f"brake {halted_on}  ({time.time() - t0:.0f}s)", flush=True)
    with open(os.path.join(out_dir, f"baseline_{window}.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    return out


# ------------------------------------------------------------- arbiter study

def shadow(asset: str, m1: dict) -> dict:
    """Run the current agents without trading; sample every 5-minute close."""
    eng = make_engine(asset)
    eng.bb.halt("shadow")
    ts, c, v = m1["ts"], m1["close"], m1["volume"]
    cols = {k: [] for k in ("i", "qs", "qc", "cs", "cc", "score", "vh")}
    for i in range(len(ts)):
        eng.on_tick(Tick((int(ts[i]) + 60) * NS_PER_S, asset, float(c[i]), float(v[i])))
        if (int(ts[i]) + 60) % 300 == 0 and i >= WARM_DAYS * DAY:
            q, ch = eng.bb.get("QUANT"), eng.bb.get("CHARTIST")
            cols["i"].append(i)
            cols["qs"].append(q.strength); cols["qc"].append(q.confidence)
            cols["cs"].append(ch.strength); cols["cc"].append(ch.confidence)
            cols["score"].append(eng.arbiter._last_score)
            cols["vh"].append(eng.warden.vol_horizon)
    return {k: np.array(x) for k, x in cols.items()}


def hypothetical(asset: str, m1: dict, s: dict) -> dict:
    """What a trade in the score's direction would have made, with WARDEN's
    own stop (2 x horizon volatility), target (3 x) and twice the horizon as
    a time limit, at the measured costs. Stop first when ambiguous."""
    inst, T = PERP[asset], 2 * HORIZON[asset]
    o, h, l = m1["open"], m1["high"], m1["low"]
    n = len(o)
    net = np.full(len(s["i"]), np.nan)
    gross = np.full(len(s["i"]), np.nan)
    fwd = np.full(len(s["i"]), np.nan)             # plain forward return over one horizon, in bps
    H = HORIZON[asset]
    for k, (i, score, vh) in enumerate(zip(s["i"], s["score"], s["vh"])):
        j = int(i) + 1
        if j + T + 1 >= n:
            continue
        px = o[j]
        fwd[k] = (o[j + H] / px - 1) * 1e4
        if score == 0 or vh <= 0:
            continue
        side = 1 if score > 0 else -1
        stop, tgt = px - side * 2 * vh, px + side * 3 * vh
        lo, hi, op = l[j:j + T], h[j:j + T], o[j:j + T]
        if side > 0:
            sh = lo <= stop
            th = hi >= tgt * (1 + inst.through)
        else:
            sh = hi >= stop
            th = lo <= tgt * (1 - inst.through)
        si = int(np.argmax(sh)) if sh.any() else T + 1
        ti = int(np.argmax(th)) if th.any() else T + 1
        if si <= ti and si <= T:
            gap = si > 0 and ((op[si] <= stop) if side > 0 else (op[si] >= stop))
            xp, cost = (op[si] if gap else stop), inst.market_cost() + inst.stop_cost()
        elif ti <= T:
            xp, cost = tgt, inst.market_cost() + inst.limit_cost()
        else:
            xp, cost = o[j + T], 2 * inst.market_cost()
        gross[k] = side * (xp / px - 1) * 1e4
        net[k] = gross[k] - cost * 1e4
    return {"net": net, "gross": gross, "fwd": fwd}


def cmean(x: np.ndarray, day: np.ndarray) -> tuple[float, float, int]:
    """Mean, and a t-statistic that treats each UTC day as one cluster
    (samples five minutes apart are nowhere near independent)."""
    ok = np.isfinite(x)
    x, day = x[ok], day[ok]
    n = len(x)
    if n < 30:
        return (float(np.mean(x)) if n else float("nan")), float("nan"), n
    m = x.mean()
    _, inv = np.unique(day, return_inverse=True)
    g = np.bincount(inv, weights=x - m)
    se = np.sqrt((g ** 2).sum()) / n
    return float(m), float(m / se) if se > 0 else float("nan"), n


def arbiter(data_dir: str, out_dir: str) -> dict:
    m1 = load_m1(data_dir, FINAL_START)
    out = {}
    for a in ASSETS:
        t0 = time.time()
        s = shadow(a, m1[a])
        hyp = hypothetical(a, m1[a], s)
        day = m1[a]["ts"][s["i"]] // 86_400
        qv, cv, score = s["qs"] * s["qc"], s["cs"] * s["cc"], s["score"]
        net, gross, fwd = hyp["net"], hyp["gross"], hyp["fwd"]
        res = {"samples": int(len(score)), "span": f"{day_str(m1[a]['ts'][s['i'][0]])} to {day_str(m1[a]['ts'][s['i'][-1]])}"}

        def row(mask):
            m, t, n = cmean(net[mask], day[mask])
            g, _, _ = cmean(gross[mask], day[mask])
            return {"n": n, "gross_bps": round(g, 2), "net_bps": round(m, 2), "t": round(t, 2)}

        ab = np.abs(score)
        res["by_score"] = {lab: row((ab >= lo) & (ab < hi)) for lab, lo, hi in
                           [("0.00-0.10", 1e-9, .10), ("0.10-0.20", .10, .20), ("0.20-0.28", .20, .28),
                            ("0.28-0.40", .28, .40), ("0.40+", .40, 9)]}
        res["threshold"] = {"below_0.28": row((ab > 1e-9) & (ab < .28)), "at_or_above_0.28": row(ab >= .28)}
        agree = (np.sign(qv) == np.sign(cv)) & (np.abs(qv) > .05) & (np.abs(cv) > .05)
        opposed = (np.sign(qv) == -np.sign(cv)) & (np.abs(qv) > .05) & (np.abs(cv) > .05)
        res["by_agreement"] = {"agree": row(agree), "opposed": row(opposed), "one_silent": row(~agree & ~opposed & (ab > 1e-9)),
                               "agree_and_above_0.28": row(agree & (ab >= .28))}

        # Q2: does CHARTIST's vote predict the next horizon's return once QUANT's is in?
        ok = np.isfinite(fwd)
        X = np.column_stack([np.ones(ok.sum()), qv[ok], cv[ok]])
        y, d = fwd[ok], day[ok]
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        e = y - X @ beta
        _, inv = np.unique(d, return_inverse=True)
        meat = np.zeros((3, 3))
        for j in range(3):
            for k2 in range(3):
                meat[j, k2] = (np.bincount(inv, weights=X[:, j] * e) * np.bincount(inv, weights=X[:, k2] * e)).sum()
        bread = np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(bread @ meat @ bread))
        res["regression_fwd_bps"] = {"quant_vote": {"coef": round(float(beta[1]), 2), "t": round(float(beta[1] / se[1]), 2)},
                                     "chartist_vote": {"coef": round(float(beta[2]), 2), "t": round(float(beta[2] / se[2]), 2)},
                                     "horizon_min": HORIZON[a]}

        # Q3: does confidence predict the return in that agent's own direction?
        cost = 2 * PERP[a].market_cost() * 1e4
        def conf_table(strength, conf, edges):
            t = {}
            own = np.sign(strength) * fwd
            for lo, hi in zip(edges[:-1], edges[1:]):
                mk = (conf >= lo) & (conf < hi) & (strength != 0)
                m, tt, n = cmean(own[mk], day[mk])
                t[f"{lo:.2f}-{min(hi, 1.0):.2f}"] = {"n": n, "gross_bps": round(m, 2), "t": round(tt, 2),
                                                       "net_bps": round(m - cost, 2)}
            return t
        res["quant_confidence"] = conf_table(s["qs"], s["qc"], [0, .2, .4, .6, .8, 1.01])
        res["chartist_confidence"] = conf_table(s["cs"], s["cc"], [0, .25, .5, .75, .95, 1.01])
        mk = (s["cc"] >= .95) & (np.abs(cv) <= .10) & (s["cs"] != 0)
        own = np.sign(s["cs"]) * fwd
        m, tt, n = cmean(own[mk], day[mk])
        res["chartist_sure_but_weak"] = {"n": n, "share_of_samples": round(float(mk.mean()), 4),
                                         "gross_bps_in_its_direction": round(m, 2), "t": round(tt, 2),
                                         "net_bps": round(m - cost, 2), "round_trip_cost_bps": round(cost, 1)}
        mk2 = (s["cc"] >= .95) & (s["cs"] != 0)
        res["chartist_at_95_plus"] = {"share_of_samples": round(float(mk2.mean()), 4),
                                      "median_abs_vote": round(float(np.median(np.abs(cv[mk2]))), 3) if mk2.any() else None}
        out[a] = res
        print(f"{a}: {res['samples']:,} samples ({time.time() - t0:.0f}s)", flush=True)
    with open(os.path.join(out_dir, "arbiter_study.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    cmd, data_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    if cmd == "baseline":
        baseline(data_dir, out_dir, sys.argv[4])
    else:
        print(json.dumps(arbiter(data_dir, out_dir), indent=1))
