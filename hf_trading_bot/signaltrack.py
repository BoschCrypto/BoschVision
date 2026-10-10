"""Paper-track a third-party allocation signal against doing nothing.

Waldo follows "Prof. Adam ~ Crypto Investing" (a paid Discord), which publishes a
target allocation and tells subscribers to "check in every day, signals can change
frequently". This module answers the only question that matters: after the real
cost of trading, does following it beat not following it?

THE COST THAT DECIDES IT. Robinhood crypto quotes carry a ~1.89% bid/ask spread
(measured 2026-10-09: BTC 1.87%, SUI 1.91%, SOL 1.91%, NEAR 1.94%, against
0.00-0.02% on an order-book venue). Every rebalance pays half the spread on the
way out and half on the way in, so turnover is charged at SPREAD_PCT of notional
traded. A signal must beat buy-and-hold by more than that drag to be worth
anything, however good its calls are.

TWO BENCHMARKS, both necessary:
  - BTC buy and hold: the sector's beta. Beating the alts is not an achievement if
    BTC did it too.
  - FROZEN sleeve: his allocation on day one, never touched. This isolates the
    value of the SIGNAL from the value of the ASSETS he already picked.

Honest limits, stated because they bound every number this produces:
  - n is tiny. One signal is not a track record. Treat anything under ~20
    observations as a log, not a result, and say so when reporting.
  - Signals are recorded when Waldo forwards them, so a missed forward looks like
    "no change". The log records `source` for exactly this reason.
  - Prices are marks at log time, not fills. Real fills land at bid or ask, which
    is why SPREAD_PCT is charged separately rather than folded into the mark.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("data/signals/adam.jsonl")

# Round-trip cost of moving $1 of notional on Robinhood crypto. Measured, not assumed.
SPREAD_PCT = 0.0189

# Below this, a rebalance costs more than the drift it corrects.
MIN_TRADE_USD = 10.0


@dataclass
class Signal:
    """One published allocation. `weights` are fractions of the whole sleeve,
    CASH included -- Adam's posts sum to 100% with cash as a named slice."""
    at: str                      # ISO8601, when the signal was PUBLISHED
    weights: dict                # {"BTC": 0.318, ..., "CASH": 0.318}
    prices: dict                 # mark price per asset at log time
    source: str = "discord"      # how it reached us; "missed" marks a known gap
    note: str = ""

    def validate(self) -> None:
        s = sum(self.weights.values())
        if abs(s - 1.0) > 0.005:
            raise ValueError(f"weights sum to {s:.4f}, not 1.0 -- cash slice missing?")
        for k in self.weights:
            if k != "CASH" and k not in self.prices:
                raise ValueError(f"no price for {k}")


def log_signal(sig: Signal, path: Path = LOG) -> None:
    sig.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(sig)) + "\n")


def read_signals(path: Path = LOG) -> list[Signal]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(Signal(**json.loads(line)))
    return sorted(out, key=lambda s: s.at)


def _value(units: dict, cash: float, prices: dict) -> float:
    return cash + sum(u * prices[a] for a, u in units.items() if a in prices)


def follow(signals: list[Signal], start_usd: float,
           spread: float = SPREAD_PCT, min_trade: float = MIN_TRADE_USD) -> dict:
    """Walk the signal history, rebalancing to each new target and paying spread.

    Returns the final value plus the running cost, so the drag is never hidden
    inside the return.
    """
    if not signals:
        return {"value": start_usd, "cost": 0.0, "trades": 0, "n": 0}
    units: dict[str, float] = {}
    cash = start_usd
    cost = 0.0
    trades = 0
    for sig in signals:
        total = _value(units, cash, sig.prices)
        for asset, w in sig.weights.items():
            if asset == "CASH":
                continue
            want = total * w
            have = units.get(asset, 0.0) * sig.prices[asset]
            delta = want - have
            if abs(delta) < min_trade:
                continue          # drift too small to be worth the spread
            cost += abs(delta) * spread
            trades += 1
            units[asset] = units.get(asset, 0.0) + delta / sig.prices[asset]
            cash -= delta
    # charge the accumulated spread against cash at the end of the walk
    cash -= cost
    last = signals[-1].prices
    return {"value": _value(units, cash, last), "cost": cost,
            "trades": trades, "n": len(signals), "units": units, "cash": cash}


def hold(asset: str, signals: list[Signal], start_usd: float) -> dict:
    """Buy once at the first signal's price, never trade again. Pays half a spread
    on entry only -- the honest comparison, since the follower pays it too."""
    if not signals:
        return {"value": start_usd}
    p0 = signals[0].prices[asset]
    entry_cost = start_usd * SPREAD_PCT / 2
    units = (start_usd - entry_cost) / p0
    return {"value": units * signals[-1].prices[asset], "cost": entry_cost,
            "units": units, "entry": p0}


def frozen(weights: dict, signals: list[Signal], start_usd: float) -> dict:
    """His own day-one allocation, never rebalanced."""
    if not signals:
        return {"value": start_usd}
    p0 = signals[0].prices
    entry_cost = start_usd * (1 - weights.get("CASH", 0.0)) * (SPREAD_PCT / 2)
    units = {a: (start_usd * w) / p0[a] for a, w in weights.items() if a != "CASH"}
    cash = start_usd * weights.get("CASH", 0.0) - entry_cost
    return {"value": _value(units, cash, signals[-1].prices), "cost": entry_cost}


def report(start_usd: float, own_weights: dict | None = None,
           path: Path = LOG) -> str:
    sigs = read_signals(path)
    if len(sigs) < 2:
        return (f"Signal log holds {len(sigs)} entry. Need at least 2 to compare "
                f"anything -- a single allocation has no performance. "
                f"Forward the next update and this starts producing numbers.")
    f = follow(sigs, start_usd)
    b = hold("BTC", sigs, start_usd)
    lines = [
        f"Signals logged: {f['n']}  ({sigs[0].at[:10]} -> {sigs[-1].at[:10]})",
        f"Rebalances executed: {f['trades']}   spread paid: ${f['cost']:,.2f}",
        "",
        f"  following the signal : ${f['value']:>10,.2f}  ({f['value']/start_usd-1:+.2%})",
        f"  BTC, bought and held : ${b['value']:>10,.2f}  ({b['value']/start_usd-1:+.2%})",
    ]
    if own_weights:
        z = frozen(own_weights, sigs, start_usd)
        lines.append(f"  your sleeve, frozen  : ${z['value']:>10,.2f}  ({z['value']/start_usd-1:+.2%})")
    lines += [
        "",
        f"  spread paid as % of starting capital: {f['cost']/start_usd:.2%}",
    ]
    if f["n"] < 20:
        lines.append("")
        lines.append(f"n={f['n']}. This is a LOG, not a result. Do not act on it.")
    return "\n".join(lines)
