"""Edge search, as specified in PLAN.md.

    python -m velocity.research.edge_search research   DIR
    python -m velocity.research.edge_search validation DIR --rule C:100
    python -m velocity.research.edge_search holdout    DIR --rule C:100

DIR holds <SYMBOL>_1h.csv files (timestamp, price, size) of hourly closes.

The stages are separate commands on purpose: the holdout is not computed,
printed or even sliced until a rule has been named on the command line.
"""
from __future__ import annotations

import calendar
import csv
import math
import sys
from typing import Callable, Optional

DAY = 86_400
FEE = 0.0010 + 0.0002            # per unit of position changed: 10 bps fee + 2 bps slippage
FUNDING_DAY = 0.0000125 * 24     # a long pays 0.00125% an hour; a short receives nothing
PERIODS = {
    "research":   ((2017, 1, 1), (2023, 1, 1)),
    "validation": ((2023, 1, 1), (2025, 1, 1)),
    "holdout":    ((2025, 1, 1), (2026, 11, 13)),
}
HORIZONS_H = [1, 4, 12, 24, 72, 168, 336, 672]


def _ts(ymd) -> int:
    return calendar.timegm((*ymd, 0, 0, 0))


# ------------------------------------------------------------------- data

def load_hourly(path: str) -> tuple[list[int], list[float]]:
    """Hourly closes on a complete grid; missing hours carry the last price."""
    raw: dict[int, float] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw[int(row["timestamp"])] = float(row["price"])
    ts = sorted(raw)
    out_t, out_p, last = [], [], raw[ts[0]]
    for t in range(ts[0], ts[-1] + 1, 3600):
        last = raw.get(t, last)
        out_t.append(t)
        out_p.append(last)
    return out_t, out_p


def daily_closes(ts: list[int], px: list[float]) -> tuple[list[int], list[float]]:
    """Close at 00:00 UTC. A candle stamped 23:00 closes at midnight, so the
    day's close is that bar; it is labelled with the midnight it ends at."""
    d_t, d_p = [], []
    for t, p in zip(ts, px):
        if t % DAY == DAY - 3600:
            d_t.append(t + 3600)
            d_p.append(p)
    return d_t, d_p


# ---------------------------------------------------------------- signals
# Every rule maps closes[0..t] -> a position held over day t+1. Nothing in a
# rule may read index t+1 or later; test_research.py checks this by changing
# the future and confirming the past does not move.

def momentum(closes: list[float], L: int, short: bool) -> list[int]:
    pos = [0] * len(closes)
    for t in range(L, len(closes)):
        up = closes[t] > closes[t - L]
        pos[t] = 1 if up else (-1 if short and closes[t] < closes[t - L] else 0)
    return pos


def ma_trend(closes: list[float], N: int) -> list[int]:
    pos, s = [0] * len(closes), 0.0
    for t, c in enumerate(closes):
        s += c
        if t >= N:
            s -= closes[t - N]
        if t >= N - 1:
            pos[t] = 1 if c > s / N else 0
    return pos


def breakout(closes: list[float], N: int) -> list[int]:
    pos, cur, half = [0] * len(closes), 0, max(2, N // 2)
    for t in range(N, len(closes)):
        if closes[t] >= max(closes[t - N:t]):
            cur = 1
        elif closes[t] <= min(closes[t - half:t]):
            cur = 0
        pos[t] = cur
    return pos


def reversal(closes: list[float], window: int = 30, z: float = 2.0) -> list[int]:
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    pos = [0] * len(closes)
    for t in range(window + 1, len(closes)):
        w = rets[t - window:t]
        m = sum(w) / window
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / (window - 1))
        if sd > 0:
            score = rets[t] / sd
            pos[t] = 1 if score < -z else (-1 if score > z else 0)
    return pos


RULES: dict[str, Callable[[list[float]], list[int]]] = {}
for _L in (7, 14, 28, 56):
    RULES[f"A:{_L}"] = (lambda c, L=_L: momentum(c, L, False))
    RULES[f"B:{_L}"] = (lambda c, L=_L: momentum(c, L, True))
for _N in (20, 50, 100, 200):
    RULES[f"C:{_N}"] = (lambda c, N=_N: ma_trend(c, N))
for _N in (20, 55):
    RULES[f"D:{_N}"] = (lambda c, N=_N: breakout(c, N))
RULES["E:2"] = reversal
NAMES = {"A": "Momentum, long or flat", "B": "Momentum, long or short",
         "C": "Above moving average", "D": "Channel breakout", "E": "Reversal after extreme day"}
NEIGHBOURS = {"A": [7, 14, 28, 56], "B": [7, 14, 28, 56], "C": [20, 50, 100, 200], "D": [20, 55]}


# --------------------------------------------------------------- backtest

def run(closes: list[float], pos: list[int], lo: int, hi: int) -> dict:
    """Trade positions pos[t] over days t+1 for t in [lo, hi-1). Returns
    daily net returns and summary numbers. Costs per PLAN.md."""
    net, gross, bh, held = [], [], [], []
    prev, changes = 0, 0
    for t in range(lo, hi - 1):
        r = closes[t + 1] / closes[t] - 1.0
        p = pos[t]
        turn = abs(p - prev)
        changes += 1 if turn else 0
        g = p * r
        net.append(g - FEE * turn - FUNDING_DAY * max(p, 0))
        gross.append(g)
        bh.append(r - FUNDING_DAY)
        held.append(p)
        prev = p
    return {"net": net, "gross": gross, "bh": bh, "pos": held, "changes": changes}


def stats(daily: list[float]) -> dict:
    n = len(daily)
    if n < 2:
        return {"n": n, "total": 0.0, "cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "mdd": 0.0, "t": 0.0}
    m = sum(daily) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in daily) / (n - 1))
    eq, peak, mdd = 1.0, 1.0, 0.0
    for x in daily:
        eq *= 1.0 + x
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
    years = n / 365.0
    return {"n": n, "total": eq - 1.0,
            "cagr": (eq ** (1 / years) - 1.0) if eq > 0 else -1.0,
            "vol": sd * math.sqrt(365), "sharpe": (m / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "mdd": mdd, "t": (m / (sd / math.sqrt(n))) if sd > 0 else 0.0}


def evaluate(closes: list[float], d_t: list[int], rule: str, period: str) -> dict:
    lo = next(i for i, t in enumerate(d_t) if t >= _ts(PERIODS[period][0]))
    hi = next((i for i, t in enumerate(d_t) if t >= _ts(PERIODS[period][1])), len(d_t))
    r = run(closes, RULES[rule](closes), lo, hi)
    n = len(r["net"])
    expo = sum(r["pos"]) / n                       # average net exposure
    matched = [expo * x for x in r["bh"]] if expo >= 0 else [expo * (x + FUNDING_DAY) for x in r["bh"]]
    diff = [a - b for a, b in zip(r["net"], matched)]
    md = sum(diff) / n
    sdd = math.sqrt(sum((x - md) ** 2 for x in diff) / (n - 1))
    return {"rule": rule, "net": stats(r["net"]), "gross": stats(r["gross"]), "bh": stats(r["bh"]),
            "matched": stats(matched), "in_market": sum(1 for p in r["pos"] if p) / n,
            "exposure": expo, "changes_per_year": r["changes"] / (n / 365.0),
            "timing_ann": md * 365, "timing_t": (md / (sdd / math.sqrt(n))) if sdd > 0 else 0.0}


def edge_map(ts: list[int], px: list[float], period: str) -> list[dict]:
    lo, hi = _ts(PERIODS[period][0]), _ts(PERIODS[period][1])
    idx = [i for i, t in enumerate(ts) if lo <= t < hi]
    p = [px[i] for i in idx]
    rows = []
    for h in HORIZONS_H:
        tr = []
        for k in range(h, len(p) - h, h):
            prev, nxt = p[k] / p[k - h] - 1.0, p[k + h] / p[k] - 1.0
            if prev != 0.0:
                tr.append((1.0 if prev > 0 else -1.0) * nxt)
        n = len(tr)
        if n < 3:
            continue
        m = sum(tr) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in tr) / (n - 1))
        rows.append({"h": h, "n": n, "mean_bps": m * 1e4, "t": m / (sd / math.sqrt(n)) if sd else 0.0,
                     "hit": sum(1 for x in tr if x > 0) / n})
    return rows


# ------------------------------------------------------------------ report

def _label(h: int) -> str:
    return f"{h}h" if h < 24 else f"{h // 24}d"


def _row(e: dict) -> str:
    n, b = e["net"], e["bh"]
    return (f"{e['rule']:<7}{n['cagr']:>+8.1%}{n['vol']:>7.0%}{n['sharpe']:>7.2f}{n['mdd']:>7.0%}{n['t']:>6.1f}"
            f"{e['in_market']:>6.0%}{e['changes_per_year']:>6.0f}  |{b['cagr']:>+8.1%}{b['sharpe']:>6.2f}{b['mdd']:>6.0%}"
            f"  |{e['timing_ann']:>+7.1%}{e['timing_t']:>6.1f}")


HEAD = (f"{'rule':<7}{'CAGR':>8}{'vol':>7}{'Sharpe':>7}{'maxDD':>7}{'t':>6}{'in':>6}{'chg/y':>6}"
        f"  |{'B&H':>8}{'Shrp':>6}{'DD':>6}  |{'timing':>7}{'t':>6}")


def main(argv: Optional[list[str]] = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    stage, folder = argv[0], argv[1]
    rule = argv[argv.index("--rule") + 1] if "--rule" in argv else None
    syms = argv[argv.index("--symbols") + 1].split(",") if "--symbols" in argv else ["BTC-USD", "ETH-USD"]
    if stage not in PERIODS:
        raise SystemExit("stage must be research, validation or holdout")
    if stage != "research" and not rule:
        raise SystemExit(f"{stage} needs --rule: the rule is named before the data is read")
    data = {}
    for s in syms:
        ts, px = load_hourly(f"{folder}/{s}_1h.csv")
        d_t, d_p = daily_closes(ts, px)
        data[s] = (ts, px, d_t, d_p)

    print(f"=== {stage.upper()}  {PERIODS[stage][0]} to {PERIODS[stage][1]} ===  "
          f"costs: {FEE*1e4:.0f} bps per side, longs pay {FUNDING_DAY*365:.1%} a year funding")
    print("\nEDGE MAP: go with the last move, hold the same length. Gross bps per trade (cost is 24).")
    print(f"{'horizon':>8}" + "".join(f"{s:>30}" for s in syms))
    maps = {s: {r['h']: r for r in edge_map(data[s][0], data[s][1], stage)} for s in syms}
    for h in HORIZONS_H:
        line = f"{_label(h):>8}"
        for s in syms:
            r = maps[s].get(h)
            line += f"{r['mean_bps']:>+10.1f} bps  t={r['t']:>+5.1f}  n={r['n']:<6}" if r else " " * 30
        print(line)

    rules = [rule] if rule else list(RULES)
    res = {s: {r: evaluate(data[s][3], data[s][2], r, stage) for r in rules} for s in syms}
    for s in syms:
        print(f"\n{s}  (after costs)\n{HEAD}")
        for r in rules:
            print(_row(res[s][r]))
    if stage == "research":
        avg = {r: sum(res[s][r]["net"]["sharpe"] for s in syms) / len(syms) for r in rules}
        print("\nRANKING by net Sharpe averaged over the assets, with the plateau check:")
        for r in sorted(avg, key=avg.get, reverse=True):
            fam, par = r.split(":")
            ok = "n/a"
            if fam in NEIGHBOURS:
                g = NEIGHBOURS[fam]
                i = g.index(int(par))
                nb = [f"{fam}:{g[j]}" for j in (i - 1, i + 1) if 0 <= j < len(g)]
                ok = "plateau" if all(avg[x] > 0 for x in nb) else "SPIKE (neighbour negative)"
            tmin = min(res[s][r]["net"]["t"] for s in syms)
            print(f"  {r:<7}{NAMES[fam]:<28} avg Sharpe {avg[r]:>5.2f}   lowest t {tmin:>4.1f}   {ok}")


if __name__ == "__main__":
    main()
