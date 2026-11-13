"""The breakout rule at every bar size.

    python -m velocity.research.timeframes DIR

DIR holds <SYMBOL>_5m.csv. One rule, not tuned: long on a new 20-bar closing
high, flat on a new 10-bar closing low. The only thing that changes between
rows is how long a bar is. Costs as everywhere else in this folder: 12 bps a
side, and a long pays about 11% a year in funding.
"""
from __future__ import annotations

import csv
import math
import sys

from .edge_search import FEE, FUNDING_DAY, breakout

BAR_MINUTES = [5, 15, 60, 240, 1440]
N = 20


def load_5m(path: str) -> tuple[list[int], list[float]]:
    raw: dict[int, float] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw[int(row["timestamp"])] = float(row["price"])
    ts = sorted(raw)
    t_out, p_out, last = [], [], raw[ts[0]]
    for t in range(ts[0], ts[-1] + 1, 300):
        last = raw.get(t, last)
        t_out.append(t)
        p_out.append(last)
    return t_out, p_out


def resample(ts: list[int], px: list[float], minutes: int) -> list[float]:
    """Closes of bars `minutes` long, aligned to UTC. A 5-minute candle
    stamped t closes at t+300, so a bar ends where (t+300) is a multiple."""
    step = minutes * 60
    return [p for t, p in zip(ts, px) if (t + 300) % step == 0]


def measure(closes: list[float], minutes: int, n: int = N) -> dict:
    pos = breakout(closes, n)
    per_day = 1440 / minutes
    net, prev, eq, peak, mdd = [], 0, 1.0, 1.0, 0.0
    trades, entry = [], None
    for t in range(n, len(closes) - 1):
        p = pos[t]
        if p and not prev:
            entry = (t, closes[t])
        elif prev and not p and entry:
            trades.append((closes[t] / entry[1] - 1.0, t - entry[0]))
            entry = None
        r = p * (closes[t + 1] / closes[t] - 1.0) - FEE * abs(p - prev) - FUNDING_DAY / per_day * p
        net.append(r)
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
        prev = p
    years = len(net) * minutes / 525_600
    g = [x for x, _ in trades]
    mean_g = sum(g) / len(g) if g else 0.0
    sd = math.sqrt(sum((x - mean_g) ** 2 for x in g) / (len(g) - 1)) if len(g) > 1 else 0.0
    return {
        "minutes": minutes, "years": years, "trades": len(trades),
        "trades_per_year": len(trades) / years if years else 0.0,
        "gross_bps": mean_g * 1e4, "t": (mean_g / (sd / math.sqrt(len(g)))) if sd else 0.0,
        "win": sum(1 for x in g if x > 0) / len(g) if g else 0.0,
        "hold_hours": (sum(b for _, b in trades) / len(trades) * minutes / 60) if trades else 0.0,
        "in_market": sum(1 for t in range(n, len(closes) - 1) if pos[t]) / max(1, len(net)),
        "net_total": eq - 1.0, "net_year": (eq ** (1 / years) - 1.0) if eq > 0 and years else -1.0,
        "mdd": mdd, "hold_total": closes[-1] / closes[n] - 1.0,
    }


def label(minutes: int) -> str:
    return f"{minutes} min" if minutes < 60 else (f"{minutes // 60} hour" if minutes < 1440 else "1 day")


def main(argv=None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    folder = argv[0]
    print(f"Breakout rule: long on a {N}-bar high, flat on a {N // 2}-bar low. "
          f"Costs {FEE * 2e4:.0f} bps a round trip.")
    for sym in ("BTC-USD", "ETH-USD"):
        ts, px = load_5m(f"{folder}/{sym}_5m.csv")
        print(f"\n{sym}")
        print(f"{'bar':>8}{'trades/yr':>11}{'avg hold':>10}{'gross/trade':>13}{'t':>6}{'wins':>7}"
              f"{'in mkt':>8}{'NET a year':>12}{'worst':>8}")
        for m in BAR_MINUTES:
            r = measure(resample(ts, px, m), m)
            hold = f"{r['hold_hours']:.1f}h" if r["hold_hours"] < 48 else f"{r['hold_hours'] / 24:.0f}d"
            print(f"{label(m):>8}{r['trades_per_year']:>11.0f}{hold:>10}{r['gross_bps']:>+10.1f}bps{r['t']:>6.1f}"
                  f"{r['win']:>7.0%}{r['in_market']:>8.0%}{r['net_year']:>+12.1%}{r['mdd']:>8.0%}")
        print(f"{'':>8}buy and hold over the same two years: {r['hold_total']:+.0%} in total")


if __name__ == "__main__":
    main()
