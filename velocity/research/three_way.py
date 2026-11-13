"""Three strategies compared on net return, as specified in PLAN2.md.

    python -m velocity.research.three_way research   PRICES FUNDING
    python -m velocity.research.three_way validation PRICES FUNDING --carry 1a --signal 2b
    python -m velocity.research.three_way holdout    PRICES FUNDING --carry 1a --signal 2b

PRICES holds <SYM>-USD_1h.csv, FUNDING holds <SYM>_<source>.csv. Add
`--source binance` to cross-check on the second funding series.

As in edge_search, the holdout is not sliced until the specs are named.
"""
from __future__ import annotations

import calendar
import csv
import sys
from typing import Optional

from .edge_search import DAY, RULES, daily_closes, load_hourly, stats

FUT_FEE = 0.0010 + 0.0002        # futures: 10 bps fee + 2 bps slippage, per side
SPOT_FEE = 0.0060                # spot, per side (Coinbase entry tier is 50-90 bps)
CARRY_NOTIONAL = 2.0 / 3.0       # two thirds of capital in the coin, one third as margin
REBAL_MOVE, REBAL_SHARE = 0.25, 0.15
CASH = {2017: .009, 2018: .019, 2019: .021, 2020: .004, 2021: .001, 2022: .020,
        2023: .051, 2024: .050, 2025: .042, 2026: .040}      # approximate T-bill yields
ENDS = {"research": (2023, 1, 1), "validation": (2025, 1, 1), "holdout": (2026, 11, 13)}
STARTS = {"validation": (2023, 1, 1), "holdout": (2025, 1, 1)}
WARM_DAYS = 372                  # a year of funding ranks plus the 7-day average


def _ts(ymd) -> int:
    return calendar.timegm((*ymd, 0, 0, 0))


def load_funding_daily(path: str, d_t: list[int]) -> list[Optional[float]]:
    """Funding summed over each day ending at d_t[i]; None before the series
    starts or after it ends."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["timestamp"]), float(r["rate"])))
    rows.sort()
    first, last = rows[0][0], rows[-1][0]
    out: list[Optional[float]] = []
    j = 0
    for t in d_t:
        s = 0.0
        while j < len(rows) and rows[j][0] <= t:
            if rows[j][0] > t - DAY:
                s += rows[j][1]
            j += 1
        # A day counts only if the series covers it: it had started within the
        # day's first 8-hour interval, and runs to the day's end (give or take
        # one interval). A half-covered day would understate that day's funding.
        out.append(s if (first <= t - DAY + 8 * 3600 and t <= last + 8 * 3600) else None)
    return out


# ------------------------------------------------------------- strategies

def carry(closes, fund, lo, hi, conditional: bool) -> list[float]:
    """Long the coin, short the perpetual. Daily return on total capital."""
    cost = CARRY_NOTIONAL * (SPOT_FEE + FUT_FEE)
    out, on, ref = [], False, 0.0
    for t in range(lo, hi - 1):
        want = True
        if conditional:
            w = fund[t - 6:t + 1]
            want = all(x is not None for x in w) and sum(w) / 7.0 > 0.0
        r = 0.0
        if want and not on:
            r -= cost
            ref = closes[t]
        elif on and not want:
            r -= cost
        on = want
        if on:
            r += CARRY_NOTIONAL * (fund[t + 1] or 0.0)        # the short receives funding
            if abs(closes[t + 1] / ref - 1.0) >= REBAL_MOVE:
                r -= REBAL_SHARE * cost
                ref = closes[t + 1]
        out.append(r)
    if on and out:
        out[-1] -= cost                                        # close out at the end
    return out


def funding_rank(fund, t) -> Optional[float]:
    """Where today's 7-day average funding sits among the previous 365 days'
    7-day averages. Past data only."""
    if t - 6 - 365 < 0 or any(x is None for x in fund[t - 6 - 365:t + 1]):
        return None
    cur = sum(fund[t - 6:t + 1]) / 7.0
    below = 0
    for k in range(t - 365, t):
        if sum(fund[k - 6:k + 1]) / 7.0 < cur:
            below += 1
    return below / 365.0


def extremes_positions(fund, n, hi_cut: float, lo_cut: float, allow_short: bool) -> list[int]:
    pos = [0] * n
    for t in range(n):
        rk = funding_rank(fund, t)
        if rk is None:
            continue
        if rk <= lo_cut:
            pos[t] = 1
        elif allow_short and rk >= hi_cut:
            pos[t] = -1
    return pos


def directional(closes, fund, pos, lo, hi) -> tuple[list[float], list[int]]:
    """A futures position: pays fees on changes; a long pays that day's
    funding, a short receives it."""
    out, prev, held = [], 0, []
    for t in range(lo, hi - 1):
        p = pos[t]
        r = p * (closes[t + 1] / closes[t] - 1.0) - FUT_FEE * abs(p - prev) - p * (fund[t + 1] or 0.0)
        out.append(r)
        held.append(p)
        prev = p
    return out, held


SIGNALS = {"2a": (0.90, 0.10, True), "2b": (0.80, 0.20, True), "2c": (1.01, 0.20, False)}
LABELS = {"1a": "Carry, always on", "1b": "Carry, only while funding is positive",
          "2a": "Funding extremes 90/10, long or short", "2b": "Funding extremes 80/20, long or short",
          "2c": "Funding fear only (long below 20%)", "3": "20-day breakout, long or flat",
          "hold": "Buy and hold (spot)", "cash": "Cash (T-bills, approx.)"}


# ----------------------------------------------------------------- driver

def window(d_t, fund, stage: str) -> tuple[int, int]:
    first_f = next(i for i, x in enumerate(fund) if x is not None)
    if stage == "research":
        lo = first_f + WARM_DAYS
    elif stage == "full":
        lo = first_f + WARM_DAYS
    else:
        lo = next(i for i, t in enumerate(d_t) if t >= _ts(STARTS[stage]))
    end = ENDS["holdout" if stage == "full" else stage]
    last_f = max(i for i, x in enumerate(fund) if x is not None)
    hi = min(next((i for i, t in enumerate(d_t) if t >= _ts(end)), len(d_t)), last_f + 1)
    return lo, hi


def all_returns(closes, d_t, fund, lo, hi, which: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for w in which:
        if w in ("1a", "1b"):
            out[w] = carry(closes, fund, lo, hi, conditional=(w == "1b"))
        elif w in SIGNALS:
            hc, lc, sh = SIGNALS[w]
            out[w], _ = directional(closes, fund, extremes_positions(fund, len(closes), hc, lc, sh), lo, hi)
        elif w == "3":
            out[w], _ = directional(closes, fund, RULES["D:20"](closes), lo, hi)
    out["hold"] = [closes[t + 1] / closes[t] - 1.0 for t in range(lo, hi - 1)]
    out["cash"] = [CASH[__import__("time").gmtime(d_t[t]).tm_year] / 365.0 for t in range(lo, hi - 1)]
    return out


def _line(name: str, daily: list[float]) -> str:
    s = stats(daily)
    return (f"  {LABELS[name]:<40}{s['cagr']:>+9.1%}{s['mdd']:>9.0%}{s['sharpe']:>8.2f}"
            f"{s['total']:>+10.0%}")


def main(argv: Optional[list[str]] = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    stage, prices, fdir = argv[0], argv[1], argv[2]
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    source = opt("--source", "deribit")
    pick_c, pick_s = opt("--carry"), opt("--signal")
    if stage not in ("research", "validation", "holdout"):
        raise SystemExit("stage must be research, validation or holdout")
    if stage != "research" and not (pick_c and pick_s):
        raise SystemExit(f"{stage} needs --carry and --signal: specs are named before the data is read")
    which = ["1a", "1b", "2a", "2b", "2c", "3"] if stage == "research" else [pick_c, pick_s, "3"]

    data = {}
    for a in ("BTC", "ETH"):
        ts, px = load_hourly(f"{prices}/{a}-USD_1h.csv")
        d_t, d_p = daily_closes(ts, px)
        data[a] = (d_t, d_p, load_funding_daily(f"{fdir}/{a}_{source}.csv", d_t))

    stages = [stage] + (["full"] if stage == "holdout" else [])
    for st in stages:
        res, span = {}, ""
        for a, (d_t, d_p, fund) in data.items():
            lo, hi = window(d_t, fund, st)
            res[a] = all_returns(d_p, d_t, fund, lo, hi, which)
            f = [x for x in fund[lo + 1:hi] if x is not None]
            g = __import__("time").gmtime
            span = f"{g(d_t[lo]).tm_year}-{g(d_t[lo]).tm_mon:02d}-{g(d_t[lo]).tm_mday:02d} to " \
                   f"{g(d_t[hi-1]).tm_year}-{g(d_t[hi-1]).tm_mon:02d}-{g(d_t[hi-1]).tm_mday:02d}"
            res[a]["_fund"] = (sum(f) / len(f) * 365, sum(1 for x in f if x > 0) / len(f))
        title = "FULL HISTORY (all three periods joined)" if st == "full" else st.upper()
        print(f"\n=== {title}: {span}   funding source: {source} ===")
        for a in ("BTC", "ETH"):
            print(f"  {a} funding averaged {res[a]['_fund'][0]:+.1%} a year; positive on {res[a]['_fund'][1]:.0%} of days")
        n = min(len(res["BTC"]["hold"]), len(res["ETH"]["hold"]))
        for a in ("BTC", "ETH", "HALF EACH"):
            print(f"\n {a:<41}{'a year':>9}{'worst':>9}{'Sharpe':>8}{'total':>10}")
            for w in which + ["hold", "cash"]:
                d = ([(x + y) / 2 for x, y in zip(res["BTC"][w][:n], res["ETH"][w][:n])]
                     if a == "HALF EACH" else res[a][w])
                print(_line(w, d))
        if st == "research":
            avg = lambda w, key: sum(stats(res[a][w])[key] for a in ("BTC", "ETH")) / 2
            c = max(("1a", "1b"), key=lambda w: avg(w, "cagr"))
            s = max(SIGNALS, key=lambda w: avg(w, "sharpe"))
            print(f"\n CHOSEN BY THE PLAN'S RULES:  carry {c} (higher net return: "
                  f"1a {avg('1a','cagr'):+.1%}, 1b {avg('1b','cagr'):+.1%})   "
                  f"signal {s} (Sharpe: " + ", ".join(f"{w} {avg(w,'sharpe'):+.2f}" for w in SIGNALS) + ")")


if __name__ == "__main__":
    main()
