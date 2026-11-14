"""Build one daily row per asset: six agent readings, and what happened next.

Every reading at time T uses only data published before T. Forward returns
start at the open of the first minute AFTER T, so a decision can never be
filled at the price that produced it.

    python -m velocity.teamwork.panel M1_DIR TEAM_DIR FUNDING_DIR OUT.npz
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np

DAY = 86_400
ASSETS = ("BTC", "ETH")
HORIZONS = (1, 3, 7)
AGENTS = ("TREND", "FLOW", "CROWD", "FEAR", "MACRO", "BREADTH")
# The bullish condition for each agent, as fixed in PLAN.md section 3.
# (sign, threshold): +1 means "rank >= threshold", -1 means "rank <= threshold".
RULES = {"TREND": (+1, 0.5), "FLOW": (+1, 0.6), "CROWD": (-1, 0.3),
         "FEAR": (-1, 0.4), "MACRO": (-1, 0.5), "BREADTH": (+1, 0.5)}


def _series(path: str, tcol: str, vcol: str) -> tuple[np.ndarray, np.ndarray]:
    t, v = [], []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t.append(int(float(r[tcol])))
            v.append(float(r[vcol]))
    o = np.argsort(t, kind="stable")
    return np.asarray(t)[o], np.asarray(v)[o]


def latest_before(src_t: np.ndarray, src_v: np.ndarray, at: np.ndarray) -> np.ndarray:
    """The most recent value published strictly before each time in `at`."""
    i = np.searchsorted(src_t, at, side="left") - 1
    return np.where(i >= 0, src_v[np.maximum(i, 0)], np.nan)


def mean_window_before(src_t: np.ndarray, src_v: np.ndarray, at: np.ndarray, span_s: int) -> np.ndarray:
    """Mean of the values in (at - span, at]. Used for the funding average."""
    cs = np.concatenate(([0.0], np.cumsum(src_v)))
    hi = np.searchsorted(src_t, at, side="right")
    lo = np.searchsorted(src_t, at - span_s, side="right")
    n = hi - lo
    return np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)


def trailing_rank(x: np.ndarray, window: int = 365) -> np.ndarray:
    """Where each value sits among the previous `window` values, 0 to 1.
    Strictly backward-looking; nan until the window is full."""
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(window, n):
        past = x[i - window:i]
        ok = np.isfinite(past)
        if ok.sum() >= window // 2 and np.isfinite(x[i]):
            out[i] = (past[ok] < x[i]).mean()
    return out


def breadth_series(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Share of USD pairs trading above their own 20-day average, per day."""
    by: dict[str, dict[int, float]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            by.setdefault(r["product"], {})[int(r["ts"])] = float(r["close"])
    days = sorted({t for s in by.values() for t in s})
    idx = {t: i for i, t in enumerate(days)}
    above = np.zeros(len(days))
    total = np.zeros(len(days))
    for s in by.values():
        ts = sorted(s)
        if len(ts) < 25:
            continue
        v = np.array([s[t] for t in ts])
        csum = np.concatenate(([0.0], np.cumsum(v)))
        for k in range(20, len(ts)):
            ma = (csum[k] - csum[k - 20]) / 20.0
            j = idx[ts[k]]
            total[j] += 1
            above[j] += 1.0 if v[k] > ma else 0.0
    ok = total >= 20
    return np.asarray(days)[ok], (above[ok] / total[ok])


def build(m1_dir: str, team_dir: str, fund_dir: str) -> dict:
    vix_t, vix_v = _series(os.path.join(team_dir, "VIX_daily.csv"), "date_ts", "vix_close")
    br_t, br_v = breadth_series(os.path.join(team_dir, "breadth_daily.csv"))
    out: dict = {"agents": list(AGENTS), "horizons": list(HORIZONS)}

    for a in ASSETS:
        z = np.load(os.path.join(m1_dir, f"{a}-USD.npz"))
        ts, op, cl, vol = z["ts"], z["open"], z["close"], z["volume"]
        base = int(ts[0])
        day0 = base + (-base) % DAY                       # first midnight in the data
        days = np.arange(day0, int(ts[-1]) - 7 * DAY, DAY)
        i_at = np.searchsorted(ts, days, side="left")     # index of the bar starting at midnight
        keep = (i_at > 0) & (i_at < len(ts) - 7 * 1440 - 1)
        days, i_at = days[keep], i_at[keep]

        close_now = cl[i_at - 1]                          # last close before the decision
        entry = op[i_at]                                  # fills on the next minute's open
        fwd = {h: op[i_at + h * 1440] / entry - 1.0 for h in HORIZONS}

        # TREND: close vs its 72-hour average, in standard deviations
        hourly_i = np.arange(0, len(ts), 60)
        hc = cl[hourly_i]
        k = np.searchsorted(hourly_i, i_at, side="right") - 1
        win = 72
        cs, cs2 = np.concatenate(([0.0], np.cumsum(hc))), np.concatenate(([0.0], np.cumsum(hc ** 2)))
        mu = (cs[k + 1] - cs[k + 1 - win]) / win
        var = np.maximum((cs2[k + 1] - cs2[k + 1 - win]) / win - mu ** 2, 1e-12)
        trend = (close_now - mu) / np.sqrt(var)

        # FLOW: last 24 hours of volume against its own 30-day median
        vcs = np.concatenate(([0.0], np.cumsum(vol)))
        day_vol = vcs[i_at] - vcs[np.maximum(i_at - 1440, 0)]
        flow = np.full(len(days), np.nan)
        for i in range(30, len(days)):
            med = np.median(day_vol[i - 30:i])
            flow[i] = day_vol[i] / med if med > 0 else np.nan

        ft, fv = _series(os.path.join(fund_dir, f"{a}_deribit.csv"), "timestamp", "rate")
        crowd = mean_window_before(ft, fv, days, 7 * DAY)
        dt_, dv = _series(os.path.join(team_dir, f"{a}_dvol.csv"), "ts", "dvol")
        fear = latest_before(dt_, dv, days)
        macro = latest_before(vix_t, vix_v, days)
        breadth = latest_before(br_t, br_v, days)

        raw = {"TREND": trend, "FLOW": flow, "CROWD": crowd, "FEAR": fear,
               "MACRO": macro, "BREADTH": breadth}
        out[a] = {"days": days, "entry": entry, "raw": raw,
                  "rank": {k2: trailing_rank(v) for k2, v in raw.items()},
                  "fwd": fwd, "week": days // (7 * DAY)}
    return out


def save(panel: dict, path: str) -> None:
    flat = {}
    for a in ASSETS:
        p = panel[a]
        flat[f"{a}/days"] = p["days"]
        flat[f"{a}/entry"] = p["entry"]
        flat[f"{a}/week"] = p["week"]
        for g in AGENTS:
            flat[f"{a}/raw/{g}"] = p["raw"][g]
            flat[f"{a}/rank/{g}"] = p["rank"][g]
        for h in HORIZONS:
            flat[f"{a}/fwd/{h}"] = p["fwd"][h]
    np.savez_compressed(path, **flat)


def load(path: str) -> dict:
    z = np.load(path)
    out: dict = {"agents": list(AGENTS), "horizons": list(HORIZONS)}
    for a in ASSETS:
        out[a] = {"days": z[f"{a}/days"], "entry": z[f"{a}/entry"], "week": z[f"{a}/week"],
                  "raw": {g: z[f"{a}/raw/{g}"] for g in AGENTS},
                  "rank": {g: z[f"{a}/rank/{g}"] for g in AGENTS},
                  "fwd": {h: z[f"{a}/fwd/{h}"] for h in HORIZONS}}
    return out


if __name__ == "__main__":
    p = build(sys.argv[1], sys.argv[2], sys.argv[3])
    save(p, sys.argv[4])
    import time as _t
    for a in ASSETS:
        d = p[a]
        cov = {g: f"{np.isfinite(d['rank'][g]).mean():.0%}" for g in AGENTS}
        print(f"{a}: {len(d['days'])} days, {_t.strftime('%Y-%m-%d', _t.gmtime(d['days'][0]))} to "
              f"{_t.strftime('%Y-%m-%d', _t.gmtime(d['days'][-1]))}; rank coverage {cov}")
