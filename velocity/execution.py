"""Execution — turning a Decision into fills, and managing the open trade.

The simulator here is deliberately pessimistic, because an optimistic fill
model is how a backtest produces a profitable strategy that loses money
live. Three costs are charged on every trade and none of them are optional:

  spread   you cross it. Buying lifts the ask, selling hits the bid. A
           simulator that fills at the mid is reporting the midpoint of a
           trade nobody can do.
  slippage proportional to size and to current volatility, because the
           book is thinner exactly when you most want out.
  latency  the price moves between your decision and your fill. Modelled
           as a drift over the configured round-trip, applied adversely.
           This is the cost that a "ridiculous speeds" design exists to
           minimise, so it is measured rather than assumed away.

Stops and targets are evaluated on every tick, not at decision time. A stop
that is only checked when a new signal arrives is not a stop.
"""
from __future__ import annotations

import random
from typing import Callable, Optional

from .bus import Blackboard
from .types import NS_PER_MS, NS_PER_S, Decision, Side, Tick


class Fill:
    __slots__ = ("ts_ns", "symbol", "side", "qty", "price", "notional", "cost", "reason")

    def __init__(self, ts_ns: int, symbol: str, side: int, qty: float,
                 price: float, notional: float, cost: float, reason: str) -> None:
        self.ts_ns = ts_ns
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.price = price
        self.notional = notional
        self.cost = cost
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        s = "BUY" if self.side > 0 else "SELL"
        return f"Fill({s} {self.qty:.2f}@{self.price:.4f} cost={self.cost:.2f} {self.reason})"


class Trade:
    __slots__ = ("symbol", "side", "qty", "entry", "exit", "entry_ns", "exit_ns",
                 "pnl", "costs", "reason_in", "reason_out", "mae", "mfe")

    def __init__(self, symbol: str, side: int, qty: float, entry: float,
                 entry_ns: int, reason_in: str) -> None:
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.entry = entry
        self.exit = 0.0
        self.entry_ns = entry_ns
        self.exit_ns = 0
        self.pnl = 0.0
        self.costs = 0.0
        self.reason_in = reason_in
        self.reason_out = ""
        self.mae = 0.0    # max adverse excursion
        self.mfe = 0.0    # max favourable excursion

    @property
    def held_s(self) -> float:
        return (self.exit_ns - self.entry_ns) / NS_PER_S if self.exit_ns else 0.0

    @property
    def won(self) -> bool:
        return self.pnl > 0


class ExecConfig:
    __slots__ = ("latency_ms", "slippage_bps", "fee_bps", "impact_coef",
                 "assume_spread_bps", "seed")

    def __init__(
        self,
        latency_ms: float = 25.0,        # decision -> fill, realistic retail
        slippage_bps: float = 1.0,       # base adverse move
        fee_bps: float = 0.0,            # most US equity brokers: 0
        impact_coef: float = 0.4,        # extra slippage per 1% ADV-ish proxy
        assume_spread_bps: float = 2.0,  # used when the feed has no quotes
        seed: Optional[int] = None,
    ) -> None:
        self.latency_ms = latency_ms
        self.slippage_bps = slippage_bps
        self.fee_bps = fee_bps
        self.impact_coef = impact_coef
        self.assume_spread_bps = assume_spread_bps
        self.seed = seed


class PaperExecutor:
    """Simulated fills against the live tick stream. No order ever leaves."""

    def __init__(self, cfg: Optional[ExecConfig] = None,
                 on_fill: Optional[Callable[[Fill], None]] = None) -> None:
        self.cfg = cfg or ExecConfig()
        self.on_fill = on_fill
        self.trades: list[Trade] = []
        self.open_trade: Optional[Trade] = None
        self.stop = 0.0
        self.target = 0.0
        self._rng = random.Random(self.cfg.seed)
        self.total_costs = 0.0

    # -- fill model -------------------------------------------------------

    def _fill_price(self, tick: Tick, side: int, notional: float, vol_unit: float) -> tuple[float, float]:
        """Return (fill price, cost in dollars). `side` +1 buy, -1 sell."""
        cfg = self.cfg
        px = tick.price
        half_spread = tick.spread * 0.5
        if half_spread <= 0:
            half_spread = px * cfg.assume_spread_bps / 20_000.0
        # Latency drift: the market keeps moving while the order is in
        # flight. Modelled as a random walk step scaled by volatility and
        # by how long we were in flight, then applied ADVERSELY half the
        # time and favourably half the time — but the adverse half is
        # weighted heavier, because you are more likely to be filled when
        # the market is coming at you (adverse selection).
        drift = 0.0
        if vol_unit > 0 and cfg.latency_ms > 0:
            step = vol_unit * (cfg.latency_ms / 1000.0) ** 0.5
            z = self._rng.gauss(0.25, 1.0)      # mean>0 == adverse bias
            drift = step * z
        slip = px * cfg.slippage_bps / 10_000.0
        impact = px * cfg.impact_coef * (notional / 1_000_000.0) / 100.0
        adverse = half_spread + slip + impact + max(0.0, drift)
        fill = px + adverse * side
        fee = notional * cfg.fee_bps / 10_000.0
        cost = adverse * (notional / px) + fee
        return fill, cost

    # -- lifecycle --------------------------------------------------------

    def open(self, d: Decision, tick: Tick, bb: Blackboard, vol_unit: float) -> Optional[Fill]:
        if self.open_trade is not None:
            return None
        side = int(d.side)
        price, cost = self._fill_price(tick, side, d.notional, vol_unit)
        if price <= 0:
            return None
        qty = d.notional / price
        t = Trade(d.symbol, side, qty, price, tick.ts_ns, d.reason)
        t.costs = cost
        self.open_trade = t
        self.stop = d.stop
        self.target = d.target
        self.total_costs += cost
        bb.open_side = side
        bb.open_entry = price
        bb.open_entry_ns = tick.ts_ns
        bb.open_notional = d.notional
        bb.trades_today += 1
        f = Fill(tick.ts_ns, d.symbol, side, qty, price, d.notional, cost, d.reason)
        if self.on_fill:
            self.on_fill(f)
        return f

    def close(self, tick: Tick, bb: Blackboard, reason: str, vol_unit: float) -> Optional[Fill]:
        t = self.open_trade
        if t is None:
            return None
        side = -t.side
        notional = t.qty * tick.price
        price, cost = self._fill_price(tick, side, notional, vol_unit)
        t.exit = price
        t.exit_ns = tick.ts_ns
        t.costs += cost
        t.pnl = (price - t.entry) * t.qty * t.side - t.costs
        t.reason_out = reason
        self.total_costs += cost
        self.trades.append(t)
        self.open_trade = None
        self.stop = self.target = 0.0

        bb.realised_pnl += t.pnl
        bb.equity += t.pnl
        bb.open_side = 0
        bb.open_entry = 0.0
        bb.open_entry_ns = 0
        bb.open_notional = 0.0
        bb.last_exit_ns = tick.ts_ns
        if t.pnl < 0:
            bb.consecutive_losses += 1
        else:
            bb.consecutive_losses = 0
        f = Fill(tick.ts_ns, t.symbol, side, t.qty, price, notional, cost, reason)
        if self.on_fill:
            self.on_fill(f)
        return f

    # -- per-tick management ---------------------------------------------

    def manage(self, tick: Tick, bb: Blackboard, vol_unit: float) -> Optional[Fill]:
        """Stop/target check. Runs on EVERY tick, before any agent logic."""
        t = self.open_trade
        if t is None:
            return None
        px = tick.price
        excursion = (px - t.entry) * t.side
        if excursion < t.mae:
            t.mae = excursion
        if excursion > t.mfe:
            t.mfe = excursion
        if t.side > 0:
            if self.stop > 0 and px <= self.stop:
                return self.close(tick, bb, "stop", vol_unit)
            if self.target > 0 and px >= self.target:
                return self.close(tick, bb, "target", vol_unit)
        else:
            if self.stop > 0 and px >= self.stop:
                return self.close(tick, bb, "stop", vol_unit)
            if self.target > 0 and px <= self.target:
                return self.close(tick, bb, "target", vol_unit)
        return None

    # -- stats ------------------------------------------------------------

    def stats(self) -> dict:
        tr = self.trades
        if not tr:
            return {"trades": 0}
        wins = [t for t in tr if t.won]
        losses = [t for t in tr if not t.won]
        gross_w = sum(t.pnl for t in wins)
        gross_l = -sum(t.pnl for t in losses)
        pnl = sum(t.pnl for t in tr)
        held = sum(t.held_s for t in tr) / len(tr)
        return {
            "trades": len(tr),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(tr),
            "pnl": pnl,
            "costs": self.total_costs,
            "pnl_before_costs": pnl + self.total_costs,
            "avg_win": (gross_w / len(wins)) if wins else 0.0,
            "avg_loss": (gross_l / len(losses)) if losses else 0.0,
            "profit_factor": (gross_w / gross_l) if gross_l > 0 else float("inf"),
            "avg_hold_s": held,
            "expectancy": pnl / len(tr),
        }
