"""Does any combination of the six agents predict the next 1 to 7 days?

    python -m velocity.teamwork.study PANEL.npz OUT_DIR

Reports, for each asset and horizon: each agent's quintiles, every pair,
and every agreement count. Statistics cluster by calendar week, because
forward returns sampled daily over a week overlap heavily.
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import numpy as np

from .panel import AGENTS, ASSETS, HORIZONS, RULES, load

COST_BPS = 24.0          # Coinbase perpetual-style futures, measured 13 Nov 2026
MIN_N = 30               # the plan's minimum non-overlapping occurrences
T_BAR = 3.0              # the plan's bar, set high because 300 conditions are examined


def cluster_stats(y: np.ndarray, week: np.ndarray) -> dict:
    """Mean and a t-statistic that treats each calendar week as one
    observation. Overlapping daily samples are not independent."""
    ok = np.isfinite(y)
    y, week = y[ok], week[ok]
    n = len(y)
    if n < 5:
        return {"n": int(n), "mean_bps": float(np.mean(y) * 1e4) if n else float("nan"), "t": float("nan"),
                "weeks": 0}
    m = float(np.mean(y))
    _, inv = np.unique(week, return_inverse=True)
    g = np.bincount(inv, weights=y - m)
    se = np.sqrt((g ** 2).sum()) / n
    return {"n": int(n), "weeks": int(len(np.unique(week))), "mean_bps": round(m * 1e4, 2),
            "t": round(float(m / se), 2) if se > 0 else float("nan"),
            "hit": round(float((y > 0).mean()), 3)}


def non_overlapping(mask: np.ndarray, horizon: int) -> int:
    """How many of the flagged days are far enough apart to count as
    separate events."""
    idx = np.nonzero(mask)[0]
    n, last = 0, -10**9
    for i in idx:
        if i - last >= horizon:
            n += 1
            last = i
    return n


def side_of(rank: dict, g: str) -> np.ndarray:
    """+1 where this agent's condition is bullish, -1 where it mirrors, 0 if unknown."""
    sign, thr = RULES[g]
    r = rank[g]
    with np.errstate(invalid="ignore"):
        bull = (r >= thr) if sign > 0 else (r <= thr)
        bear = ((r <= 1 - thr) if sign > 0 else (r >= 1 - thr)) & ~bull
    # `& ~bull` matters where the threshold is 0.5: there the bullish and
    # bearish conditions touch, and without it every reading of exactly 0.5
    # was labelled bearish.
    out = np.zeros(len(r))
    ok = np.isfinite(r)
    out[ok & bull] = 1.0
    out[ok & bear] = -1.0
    return out


def run(panel_path: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    p = load(panel_path)
    res: dict = {"cost_bps": COST_BPS, "bar": {"t": T_BAR, "min_n": MIN_N}}
    tested = 0

    for a in ASSETS:
        d = p[a]
        week, rank = d["week"], d["rank"]
        sides = {g: side_of(rank, g) for g in AGENTS}
        res[a] = {}
        for h in HORIZONS:
            fwd = d["fwd"][h]
            base = cluster_stats(fwd, week)
            block: dict = {"all_days": base, "quintiles": {}, "pairs": {}, "agreement": {}}

            # 1. each agent alone, by quintile of its reading
            for g in AGENTS:
                r = rank[g]
                qs = {}
                for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
                    m = np.isfinite(r) & (r >= lo) & (r < hi)
                    qs[f"{lo:.1f}-{min(hi,1.0):.1f}"] = cluster_stats(np.where(m, fwd, np.nan), week)
                    tested += 1
                block["quintiles"][g] = qs

            # 2. every pair: both agents pointing the same way
            for g1, g2 in itertools.combinations(AGENTS, 2):
                s1, s2 = sides[g1], sides[g2]
                both = (s1 != 0) & (s1 == s2)
                y = np.where(both, s1 * fwd, np.nan)     # scored in the agreed direction
                st = cluster_stats(y, week)
                st["net_bps"] = round(st["mean_bps"] - COST_BPS, 2) if np.isfinite(st["mean_bps"]) else None
                st["events"] = non_overlapping(both, h)
                block["pairs"][f"{g1}+{g2}"] = st
                tested += 1

            # 3. how many agents agree
            stack = np.vstack([sides[g] for g in AGENTS])
            net = stack.sum(axis=0)
            votes = np.abs(net)
            for k in (2, 3, 4, 5, 6):
                m = votes >= k
                y = np.where(m, np.sign(net) * fwd, np.nan)
                st = cluster_stats(y, week)
                st["net_bps"] = round(st["mean_bps"] - COST_BPS, 2) if np.isfinite(st["mean_bps"]) else None
                st["events"] = non_overlapping(m, h)
                block["agreement"][f"{k}+ agree"] = st
                tested += 1
            res[a][str(h)] = block

    # 4. the best conjunction that clears the plan's bar on BOTH assets
    winners = []
    for h in HORIZONS:
        for kind in ("pairs", "agreement"):
            for key in res["BTC"][str(h)][kind]:
                b, e = res["BTC"][str(h)][kind][key], res["ETH"][str(h)][kind][key]
                if all(x.get("net_bps") is not None and x["net_bps"] > 0 and np.isfinite(x["t"])
                       and x["t"] > T_BAR and x["events"] >= MIN_N for x in (b, e)):
                    winners.append({"horizon": h, "condition": key,
                                    "BTC": {k2: b[k2] for k2 in ("n", "events", "mean_bps", "net_bps", "t", "hit")},
                                    "ETH": {k2: e[k2] for k2 in ("n", "events", "mean_bps", "net_bps", "t", "hit")}})
    res["conditions_tested"] = tested
    res["winners"] = winners
    res["verdict"] = ("found" if winners else "nothing cleared the bar")
    with open(os.path.join(out_dir, "study.json"), "w") as fh:
        json.dump(res, fh, indent=1)
    return res


def report(res: dict) -> None:
    print(f"Conditions examined: {res['conditions_tested']}   "
          f"bar: net > 0, t > {res['bar']['t']}, {res['bar']['min_n']}+ events, on BOTH assets\n")
    for h in HORIZONS:
        print(f"===== {h}-day forward return =====")
        for a in ASSETS:
            b = res[a][str(h)]
            print(f"  {a}: every day  mean {b['all_days']['mean_bps']:+.1f} bps  "
                  f"t={b['all_days']['t']:+.2f}  n={b['all_days']['n']}")
        print(f"  {'agents agreeing':<18}" + "".join(f"{a:>34}" for a in ASSETS))
        for k in ("2+ agree", "3+ agree", "4+ agree", "5+ agree", "6+ agree"):
            line = f"  {k:<18}"
            for a in ASSETS:
                s = res[a][str(h)]["agreement"][k]
                net = s.get("net_bps")
                line += (f"  gross {s['mean_bps']:>+7.1f} net {net:>+7.1f} t={s['t']:>+5.2f} ev={s['events']:>3}"
                         if net is not None else " " * 34)
            print(line)
        # the three best pairs on BTC, with ETH beside them
        pairs = res["BTC"][str(h)]["pairs"]
        best = sorted((k for k in pairs if pairs[k]["events"] >= MIN_N),
                      key=lambda k: -(pairs[k]["mean_bps"] if np.isfinite(pairs[k]["mean_bps"]) else -1e9))[:3]
        for k in best:
            b, e = pairs[k], res["ETH"][str(h)]["pairs"][k]
            print(f"  {k:<28} BTC gross {b['mean_bps']:>+7.1f} net {b['net_bps']:>+7.1f} t={b['t']:>+5.2f} ev={b['events']:>3}"
                  f" | ETH gross {e['mean_bps']:>+7.1f} net {e['net_bps']:>+7.1f} t={e['t']:>+5.2f} ev={e['events']:>3}")
        print()
    print(f"VERDICT: {res['verdict']}")
    for w in res["winners"]:
        print("  ", w)


if __name__ == "__main__":
    report(run(sys.argv[1], sys.argv[2]))
