"""Walk-forward research, the freeze, and the single final test.

    python -m velocity.shortterm.run research DATA_DIR OUT_DIR [--funding DIR]
    python -m velocity.shortterm.run freeze   DATA_DIR OUT_DIR
    python -m velocity.shortterm.run final    DATA_DIR OUT_DIR

DATA_DIR holds BTC-USD.npz and ETH-USD.npz from velocity.shortterm.data.
`research` and `freeze` load nothing after FINAL_START. `final` refuses to
run until the freeze file exists, and records that it has run.
"""
from __future__ import annotations

import calendar
import csv
import json
import os
import sys
import time
from typing import Optional

import numpy as np

from . import features, strategies
from .config import PERP, SPOT, RiskBudget
from .sim import Market, portfolio, stats

DAY = 1440
START = calendar.timegm((2023, 11, 13, 0, 0, 0))
FINAL_START = calendar.timegm((2026, 4, 8, 0, 0, 0))       # the last 20% begins here
END = calendar.timegm((2026, 11, 13, 0, 0, 0))
WARM_DAYS, TRAIN_DAYS, TEST_DAYS = 14, 270, 90
ASSETS = ("BTC", "ETH")
FROZEN = "config/shortterm_frozen.json"


def day_str(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))


def load_m1(data_dir: str, upto_ts: Optional[int]) -> dict:
    out = {}
    for a in ASSETS:
        z = np.load(os.path.join(data_dir, f"{a}-USD.npz"))
        m = {k: z[k] for k in z.files}
        if upto_ts is not None:
            keep = m["ts"] < upto_ts
            m = {k: v[keep] for k, v in m.items()}
        out[a] = m
    return out


def funding_cum(fdir: Optional[str], asset: str, ts: np.ndarray) -> Optional[np.ndarray]:
    """Cumulative funding rate a long has paid, on the one-minute grid, from
    Deribit's hourly history (a proxy: Coinbase's own history is too short)."""
    if not fdir:
        return None
    path = os.path.join(fdir, f"{asset}_deribit.csv")
    if not os.path.exists(path):
        return None
    raw = np.loadtxt(path, delimiter=",", skiprows=1)
    cs = np.cumsum(raw[:, 1])
    idx = np.searchsorted(raw[:, 0], ts, side="right") - 1
    return np.where(idx >= 0, cs[np.maximum(idx, 0)], 0.0)


class Lab:
    """Data, features, markets and every configuration's setups, built once."""

    def __init__(self, data_dir: str, upto_ts: Optional[int], fdir: Optional[str] = None,
                 instruments: dict = PERP, configs: Optional[list] = None) -> None:
        self.m1 = load_m1(data_dir, upto_ts)
        self.ts = self.m1["BTC"]["ts"]
        assert np.array_equal(self.ts, self.m1["ETH"]["ts"]), "assets must share one time grid"
        self.feats = {a: features.build(self.m1[a]) for a in ASSETS}
        self.markets = {a: Market(a, self.m1[a], self.feats[a], instruments[a],
                                  funding_cum(fdir, a, self.ts)) for a in ASSETS}
        self.configs = configs if configs is not None else strategies.registry()
        self.setups = {c["id"]: {a: strategies.setups_for(self.m1[a], self.feats[a], c, a) for a in ASSETS}
                       for c in self.configs}
        self.budget = RiskBudget()

    def idx(self, ts: int) -> int:
        return int(np.searchsorted(self.ts, ts))

    def run(self, cfg_id: str, i0: int, i1: int, assets=ASSETS, **kw):
        s = {a: self.setups[cfg_id][a] for a in assets}
        m = {a: self.markets[a] for a in assets}
        return portfolio(m, s, self.budget, i0, i1, **kw)


def folds(lab: Lab, research_end_i: int) -> list[tuple[int, int, int, int]]:
    s = WARM_DAYS * DAY
    out, j = [], 0
    while True:
        tr0 = s + j * TEST_DAYS * DAY
        te0 = tr0 + TRAIN_DAYS * DAY
        te1 = min(te0 + TEST_DAYS * DAY, research_end_i)
        if te0 >= research_end_i - DAY:
            break
        out.append((tr0, te0, te0, te1))
        j += 1
    return out


def objective(st: dict, budget: RiskBudget, min_trades: int) -> float:
    """Compounded return, but only for a result inside the risk budget."""
    a = st["ALL"]
    if a["trades"] < min_trades or a["max_drawdown"] > budget.max_drawdown:
        return -1e9 + a["total_return"]
    return a["total_return"]


def research(data_dir: str, out_dir: str, fdir: Optional[str]) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    lab = Lab(data_dir, FINAL_START, fdir)
    end_i = len(lab.ts) - 1
    fl = folds(lab, end_i)
    print(f"research data {day_str(lab.ts[0])} to {day_str(lab.ts[-1])}; {len(lab.configs)} configurations; "
          f"{len(fl)} folds; built in {time.time() - t0:.0f}s", flush=True)

    # 1. every configuration: whole research period, each training window, each test fold
    rows, train_obj, test_res = [], {}, {}
    for c in lab.configs:
        cid = c["id"]
        full = stats(lab.run(cid, WARM_DAYS * DAY, end_i, log_skips=False))
        train_obj[cid] = [objective(stats(lab.run(cid, a, b, log_skips=False)), lab.budget, 30) for a, b, _, _ in fl]
        # fixed configuration, stitched over the test folds with a carried balance
        eq, trades, peak, mdd, per = lab.budget.equity, 0, lab.budget.equity, 0.0, []
        for _, _, a, b in fl:
            r = lab.run(cid, a, b, log_skips=False, start_equity=eq)
            for _, e in r.equity:
                peak = max(peak, e)
                mdd = max(mdd, 1 - e / peak)
            st = stats(r)
            per.append((st["ALL"]["trades"], r.equity[-1][1] / eq - 1))
            eq, trades = r.equity[-1][1], trades + st["ALL"]["trades"]
        test_res[cid] = per
        rows.append({"config": cid, "family": c["family"],
                     "research_trades": full["ALL"]["trades"], "research_net_pct": round(full["ALL"]["net_pct"], 2),
                     "research_max_dd_pct": round(full["ALL"]["max_drawdown"] * 100, 2),
                     "research_avg_net_bps": round(full["ALL"]["avg_net_bps"], 2),
                     "research_profit_factor": round(full["ALL"]["profit_factor"], 2),
                     "btc_trades": full["BTC"]["trades"], "btc_avg_net_bps": round(full["BTC"]["avg_net_bps"], 2),
                     "eth_trades": full["ETH"]["trades"], "eth_avg_net_bps": round(full["ETH"]["avg_net_bps"], 2),
                     "oos_fixed_trades": trades, "oos_fixed_net_pct": round((eq / lab.budget.equity - 1) * 100, 2),
                     "oos_fixed_max_dd_pct": round(mdd * 100, 2)})
        print(f"  {cid:<22} research {full['ALL']['trades']:>5} trades {full['ALL']['net_pct']:>+7.2f}%  "
              f"avg {full['ALL']['avg_net_bps']:>+6.1f} bps  | out-of-sample (fixed) {(eq / lab.budget.equity - 1) * 100:>+7.2f}%",
              flush=True)
    with open(os.path.join(out_dir, "registry.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # 2. walk-forward by family: pick on the training window, score on the next fold
    fams, fold_rows = {}, []
    for fam in strategies.FAMILY_NAMES:
        ids = [c["id"] for c in lab.configs if c["family"] == fam]
        eq, peak, mdd, trades, picks = lab.budget.equity, lab.budget.equity, 0.0, 0, []
        for k, (tr0, tr1, a, b) in enumerate(fl):
            pick = max(ids, key=lambda cid: train_obj[cid][k])
            r = lab.run(pick, a, b, log_skips=False, start_equity=eq)
            for _, e in r.equity:
                peak = max(peak, e)
                mdd = max(mdd, 1 - e / peak)
            st = stats(r)
            fold_rows.append({"family": fam, "fold": k + 1, "train": f"{day_str(lab.ts[tr0])} to {day_str(lab.ts[tr1])}",
                              "test": f"{day_str(lab.ts[a])} to {day_str(lab.ts[b])}", "test_days": (b - a) // DAY,
                              "picked": pick, "picked_in_budget": train_obj[pick][k] > -1e8,
                              "trades": st["ALL"]["trades"], "btc_trades": st["BTC"]["trades"],
                              "eth_trades": st["ETH"]["trades"],
                              "net_pct": round((r.equity[-1][1] / eq - 1) * 100, 3),
                              "avg_net_bps": round(st["ALL"]["avg_net_bps"], 2)})
            eq, trades = r.equity[-1][1], trades + st["ALL"]["trades"]
            picks.append(pick)
        fams[fam] = {"oos_net_pct": (eq / lab.budget.equity - 1) * 100, "oos_max_dd_pct": mdd * 100,
                     "oos_trades": trades, "picks": picks,
                     "positive_folds": sum(1 for f in fold_rows if f["family"] == fam and f["net_pct"] > 0)}
    with open(os.path.join(out_dir, "folds.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fold_rows[0]))
        w.writeheader()
        w.writerows(fold_rows)

    print("\nWALK-FORWARD BY FAMILY (pick on 270 days, test on the next 90, balance carried)")
    print(f"{'family':<24}{'OOS net':>9}{'max DD':>8}{'trades':>8}{'folds up':>10}   picks")
    for fam, v in sorted(fams.items(), key=lambda kv: -kv[1]["oos_net_pct"]):
        print(f"{strategies.FAMILY_NAMES[fam]:<24}{v['oos_net_pct']:>+8.2f}%{v['oos_max_dd_pct']:>7.2f}%"
              f"{v['oos_trades']:>8}{v['positive_folds']:>7}/{len(fl)}   {' '.join(p.split('-', 1)[1] for p in v['picks'])}")
    ok = {f: v for f, v in fams.items() if v["oos_max_dd_pct"] <= lab.budget.max_drawdown * 100 and v["oos_trades"] >= 100}
    ranked = sorted(ok or fams, key=lambda f: -fams[f]["oos_net_pct"])
    win = ranked[0]
    qualified = bool(ok) and fams[win]["oos_net_pct"] > 0
    summary = {"families": fams, "winner": win, "qualified": qualified, "n_configs": len(lab.configs),
               "n_folds": len(fl), "research_end": day_str(lab.ts[-1])}
    with open(os.path.join(out_dir, "research.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(f"\nBest family by the plan's rule: {strategies.FAMILY_NAMES[win]} "
          f"({'QUALIFIES' if qualified else 'does NOT qualify: not above zero inside the risk budget'})")
    return summary


def freeze(data_dir: str, out_dir: str, fdir: Optional[str]) -> dict:
    """Fix the winner's parameters on the last 270 research days. After this
    file exists, nothing about the strategy may change."""
    with open(os.path.join(out_dir, "research.json")) as fh:
        summary = json.load(fh)
    lab = Lab(data_dir, FINAL_START, fdir)
    end_i = len(lab.ts) - 1
    ids = [c for c in lab.configs if c["family"] == summary["winner"]]
    a0 = end_i - TRAIN_DAYS * DAY
    best = max(ids, key=lambda c: objective(stats(lab.run(c["id"], a0, end_i, log_skips=False)), lab.budget, 30))
    from dataclasses import asdict
    frozen = {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "config": best,
              "chosen_on": f"{day_str(lab.ts[a0])} to {day_str(lab.ts[end_i])}",
              "qualified_in_research": summary["qualified"], "risk": asdict(lab.budget),
              "instruments": {a: asdict(PERP[a]) for a in ASSETS}, "final_test_run": False}
    os.makedirs(os.path.dirname(FROZEN), exist_ok=True)
    with open(FROZEN, "w") as fh:
        json.dump(frozen, fh, indent=1)
    print(f"FROZEN: {best['id']}  (chosen on {frozen['chosen_on']})  -> {FROZEN}")
    return frozen


if __name__ == "__main__":
    cmd, data_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    fdir = sys.argv[sys.argv.index("--funding") + 1] if "--funding" in sys.argv else None
    if cmd == "research":
        research(data_dir, out_dir, fdir)
    elif cmd == "freeze":
        freeze(data_dir, out_dir, fdir)
    elif cmd == "final":
        from .final import final
        final(data_dir, out_dir, fdir)
