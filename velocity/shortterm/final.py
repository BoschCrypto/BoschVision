"""The single final test of the frozen strategy, with its stress tests.

Refuses to run without config/shortterm_frozen.json. Writes its results and
marks the freeze file as used; a second call prints the saved results
instead of running again.
"""
from __future__ import annotations

import csv
import json
import os
import time
from typing import Optional

import numpy as np

from . import strategies
from .config import PERP, SPOT
from .run import ASSETS, DAY, FINAL_START, FROZEN, TRAIN_DAYS, WARM_DAYS, Lab, day_str
from .sim import portfolio, resolve, stats


def week_bootstrap(trades: list, n_boot: int = 4000, seed: int = 7) -> dict:
    """Range for the average net bps per trade, resampling whole WEEKS so
    that trades bunched in time are not treated as independent."""
    if len(trades) < 10:
        return {"n": len(trades), "note": "too few trades"}
    wk = np.array([t["entry_ts"] // (7 * 86_400) for t in trades])
    bps = np.array([t["net_bps"] for t in trades])
    weeks = np.unique(wk)
    groups = [bps[wk == w] for w in weeks]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        x = np.concatenate([groups[i] for i in pick])
        means.append(x.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": len(trades), "weeks": int(len(weeks)), "mean": round(float(bps.mean()), 2),
            "low": round(float(lo), 2), "high": round(float(hi), 2)}


def stress(lab: Lab, cfg: dict, i0: int, i1: int) -> dict:
    """PLAN.md section 7, on one window."""
    cid = cfg["id"]
    base = lab.run(cid, i0, i1)
    st = stats(base)
    tr = base.trades
    out = {"base": _brief(st), "trades": len(tr)}
    out["double_slippage"] = _brief(stats(lab.run(cid, i0, i1, slip_mult=2.0, log_skips=False)))
    out["one_bar_late"] = _brief(stats(lab.run(cid, i0, i1, delay_bars=1, log_skips=False)))
    out["neighbours"] = {c["id"]: _brief(stats(lab.run(c["id"], i0, i1, log_skips=False)))
                         for c in lab.configs if c["family"] == cfg["family"] and c["id"] != cid}
    top = sorted(tr, key=lambda t: -t["pnl"])[:5]
    out["without_best_5"] = {"net_usd": round(sum(t["pnl"] for t in tr) - sum(t["pnl"] for t in top), 2),
                             "best_5_usd": round(sum(t["pnl"] for t in top), 2)}
    out["by_asset"] = {a: {"trades": st[a]["trades"], "net_usd": round(st[a]["net_usd"], 2),
                           "avg_net_bps": round(st[a]["avg_net_bps"], 2)} for a in ASSETS}
    months = {}
    for t in tr:
        m = time.strftime("%Y-%m", time.gmtime(t["entry_ts"]))
        months.setdefault(m, [0, 0.0])
        months[m][0] += 1
        months[m][1] += t["pnl"]
    out["by_month"] = {m: {"trades": v[0], "net_usd": round(v[1], 2)} for m, v in sorted(months.items())}
    # market condition at entry: 24-hour trend, and volatility tercile
    cond = {"trend up": [], "trend down": [], "no trend": [], "vol low": [], "vol mid": [], "vol high": []}
    vols = []
    for t in tr:
        f = lab.feats[t["asset"]]
        k = int(f["k_of_min"][t["entry_i"]])
        vols.append(float(f["atr"][k] / lab.m1[t["asset"]]["close"][t["entry_i"]]))
    if vols:
        q1, q2 = np.percentile(vols, [33.3, 66.7])
    for t, v in zip(tr, vols):
        f = lab.feats[t["asset"]]
        k = int(f["k_of_min"][t["entry_i"]])
        tr24 = f["trend24"][k]
        cond["trend up" if tr24 > 0 else "trend down" if tr24 < 0 else "no trend"].append(t["net_bps"])
        cond["vol low" if v <= q1 else "vol mid" if v <= q2 else "vol high"].append(t["net_bps"])
    out["by_condition"] = {k: {"trades": len(x), "avg_net_bps": round(float(np.mean(x)), 2) if x else None}
                           for k, x in cond.items()}
    g = np.array([t["gross_bps"] - t["funding_bps"] for t in tr]) if tr else np.array([0.0])
    c = np.array([t["cost_bps"] for t in tr]) if tr else np.array([0.0])
    out["break_even_round_trip_bps"] = round(float(g.mean()), 2)
    out["actual_round_trip_bps"] = round(float(c.mean()), 2)
    out["bootstrap_avg_net_bps"] = week_bootstrap(tr)
    exits = {}
    for t in tr:
        exits[t["how"]] = exits.get(t["how"], 0) + 1
    out["exits"] = exits
    out["limit_fill_share"] = round(exits.get("target", 0) / max(1, len(tr)), 3)
    return out


def _brief(st: dict) -> dict:
    a = st["ALL"]
    return {"trades": a["trades"], "net_pct": round(a["total_return"] * 100, 3),
            "max_dd_pct": round(a["max_drawdown"] * 100, 3), "avg_net_bps": round(a["avg_net_bps"], 2),
            "profit_factor": round(a["profit_factor"], 3), "costs_usd": round(a["costs_usd"], 2),
            "time_in_market": round(a["time_in_market"], 3)}


def table(st: dict) -> dict:
    out = {}
    for a in list(ASSETS) + ["ALL"]:
        x = st[a]
        out[a] = {"trades": x["trades"], "net_usd": round(x["net_usd"], 2), "net_pct": round(x["net_pct"], 3),
                  "avg_net_bps": round(x["avg_net_bps"], 2), "profit_factor": round(x["profit_factor"], 3),
                  "win_rate": round(x["win_rate"], 3), "costs_usd": round(x["costs_usd"], 2),
                  "time_in_market": round(x["time_in_market"], 3)}
    out["ALL"]["max_dd_pct"] = round(st["ALL"]["max_drawdown"] * 100, 3)
    out["ALL"]["total_return_pct"] = round(st["ALL"]["total_return"] * 100, 3)
    return out


def write_csv(path: str, rows: list, drop=("setup",)) -> None:
    if not rows:
        open(path, "w").close()
        return
    keys = [k for k in rows[0] if k not in drop]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            for k in ("entry_ts", "exit_ts", "known_ts", "ts"):
                if k in r:
                    r[k] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r[k]))
            for k, v in r.items():
                if isinstance(v, float):
                    r[k] = round(v, 4)
            w.writerow(r)


def hold_reference(lab: Lab, i0: int, i1: int) -> dict:
    """Buy and hold at the budget's limits, same costs: 25% of equity in one
    asset; 18.75% in each for the combined account (37.5% in total)."""
    out = {}
    for a in ASSETS:
        mk = lab.markets[a]
        r = (mk.o[i1] / mk.o[i0 + 1] - 1) - 2 * mk.inst.market_cost()
        if mk.fund is not None:
            r -= float(mk.fund[i1] - mk.fund[i0 + 1])
        out[a] = r
    return {"BTC_25pct": round(out["BTC"] * 25, 3), "ETH_25pct": round(out["ETH"] * 25, 3),
            "ALL_18.75pct_each": round((out["BTC"] + out["ETH"]) * 18.75, 3),
            "asset_move_pct": {a: round((lab.markets[a].o[i1] / lab.markets[a].o[i0 + 1] - 1) * 100, 2) for a in ASSETS}}


def final(data_dir: str, out_dir: str, fdir: Optional[str]) -> dict:
    if not os.path.exists(FROZEN):
        raise SystemExit("No freeze file. Run `freeze` first: the final test needs a frozen strategy.")
    with open(FROZEN) as fh:
        frozen = json.load(fh)
    saved = os.path.join(out_dir, "final.json")
    if frozen.get("final_test_run") and os.path.exists(saved):
        print("The final test has already been run once. Showing the saved result.")
        with open(saved) as fh:
            return json.load(fh)
    cfg = frozen["config"]
    lab = Lab(data_dir, None, fdir)
    i0, i1 = lab.idx(FINAL_START), len(lab.ts) - 1
    res = lab.run(cfg["id"], i0, i1)
    st = stats(res)
    out = {"config": cfg, "window": f"{day_str(lab.ts[i0])} to {day_str(lab.ts[i1])}",
           "days": (i1 - i0) // DAY, "result": table(st), "hold": hold_reference(lab, i0, i1),
           "stress": stress(lab, cfg, i0, i1), "equity": res.equity,
           "skip_reasons": {}}
    for s in res.skipped:
        out["skip_reasons"][s["why"]] = out["skip_reasons"].get(s["why"], 0) + 1
    write_csv(os.path.join(out_dir, "final_ledger.csv"), res.trades)
    write_csv(os.path.join(out_dir, "final_skipped.csv"), res.skipped)

    # the same frozen rule on SPOT: long only, spot fees
    spot = Lab(data_dir, None, None, instruments=SPOT, configs=[cfg])
    out["spot"] = table(stats(spot.run(cfg["id"], i0, i1, log_skips=False)))

    # the frozen rule on the research walk-forward span, for the stress comparison
    r0 = (WARM_DAYS + TRAIN_DAYS) * DAY
    out["stress_research_oos"] = stress(lab, cfg, r0, i0)
    wf = lab.run(cfg["id"], r0, i0, log_skips=False)
    out["research_oos"] = {"window": f"{day_str(lab.ts[r0])} to {day_str(lab.ts[i0])}", "result": table(stats(wf)),
                           "equity": wf.equity, "hold": hold_reference(lab, r0, i0)}
    write_csv(os.path.join(out_dir, "research_oos_ledger.csv"), wf.trades)

    with open(saved, "w") as fh:
        json.dump(out, fh, indent=1)
    frozen["final_test_run"] = True
    frozen["final_test_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(FROZEN, "w") as fh:
        json.dump(frozen, fh, indent=1)
    a = out["result"]["ALL"]
    print(f"FINAL TEST {out['window']} ({out['days']} days)  {cfg['id']}")
    print(f"  net {a['total_return_pct']:+.2f}%  max drawdown {a['max_dd_pct']:.2f}%  trades {a['trades']}  "
          f"avg {a['avg_net_bps']:+.1f} bps  PF {a['profit_factor']:.2f}  costs ${a['costs_usd']:,.0f}")
    return out
