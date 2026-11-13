"""Instruments, costs and the risk budget. Every number here is either
measured (and says when) or marked as an estimate. PLAN.md is the source."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class Instrument:
    asset: str               # "BTC" / "ETH"
    kind: str                # "perp" or "spot"
    contract: float          # units of the coin per contract; 0 = any fraction
    taker_bps: float         # measured: account fee tier, 13 Nov 2026
    maker_bps: float         # measured
    spread_bps: float        # measured: one snapshot of the futures book
    slip_bps: float          # ESTIMATE: beyond the spread, per side
    stop_slip_bps: float     # ESTIMATE: stop orders fill worse
    can_short: bool
    pays_funding: bool

    def market_cost(self, slip_mult: float = 1.0) -> float:
        """Fraction of notional lost crossing the book with a market order."""
        return (self.taker_bps + self.spread_bps / 2 + self.slip_bps * slip_mult) / 1e4

    def stop_cost(self, slip_mult: float = 1.0) -> float:
        return (self.taker_bps + self.spread_bps / 2 + self.stop_slip_bps * slip_mult) / 1e4

    def limit_cost(self) -> float:
        return self.maker_bps / 1e4

    @property
    def through(self) -> float:
        """How far past a resting limit price must trade before it counts as
        filled. A touch is not a fill."""
        return max(1.0, self.spread_bps) / 1e4


PERP = {
    "BTC": Instrument("BTC", "perp", 0.01, 10.0, 9.5, 0.73, 0.5, 1.0, True, True),
    "ETH": Instrument("ETH", "perp", 0.10, 10.0, 9.5, 5.16, 0.5, 1.0, True, True),
}
SPOT = {
    "BTC": Instrument("BTC", "spot", 0.0, 90.0, 50.0, 0.73, 0.5, 1.0, False, False),
    "ETH": Instrument("ETH", "spot", 0.0, 90.0, 50.0, 5.16, 0.5, 1.0, False, False),
}


@dataclass(frozen=True)
class RiskBudget:
    equity: float = 5_000.0
    risk_per_trade: float = 0.005      # of equity, lost if the stop is hit
    max_position: float = 0.25         # of equity, one asset
    combined_risk: float = 0.0075      # both assets open (assumption, PLAN.md s1)
    combined_position: float = 0.375
    daily_loss: float = 0.02
    max_drawdown: float = 0.08
    nominal_stop: float = 0.015        # used to budget rules that set no stop
    min_stop: float = 0.0015
    max_stop: float = 0.015


def load(path: str = "config/shortterm.json") -> tuple[dict, RiskBudget]:
    """The defaults above, overridden by a JSON file if one exists."""
    inst, budget = dict(PERP), RiskBudget()
    if os.path.exists(path):
        with open(path) as fh:
            cfg = json.load(fh)
        budget = replace(budget, **cfg.get("risk", {}))
        for a, over in cfg.get("instruments", {}).items():
            inst[a] = replace(inst[a], **over)
    return inst, budget


def dump(path: str = "config/shortterm.json") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"risk": asdict(RiskBudget()),
                   "instruments": {a: asdict(i) for a, i in PERP.items()},
                   "live_orders_enabled": False}, fh, indent=2)
