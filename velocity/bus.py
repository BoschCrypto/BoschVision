"""The blackboard — how the agents actually talk to each other.

Deliberately NOT a message queue. A queue in the hot path means a lock, a
context switch and an allocation per message; at 10k ticks/sec with five
agents that is 50k queue operations a second spent on plumbing.

Instead: a shared, last-value-wins board. Each agent writes its current
opinion to its own slot and reads anyone else's in O(1). Under CPython a
dict slot assignment is a single bytecode and therefore atomic, so the slow
lane (SENTINEL, running in another thread) can publish while the fast lane
reads without a lock and without ever seeing a half-written object.

The agents are genuinely interacting, not just voting in parallel:
  - CHARTIST reads QUANT's trend read to decide whether a level break is
    continuation or a trap.
  - QUANT reads SENTINEL's bias to widen its own thresholds when a
    headline is live (news moves are not mean-reverting).
  - WARDEN reads every signal plus realised P&L to size or to veto.
  - ARBITER reads all four and is the only one that emits an order intent.
"""
from __future__ import annotations

from typing import Optional

from .types import NS_PER_S, Signal


class Blackboard:
    """Shared agent state. One instance per traded symbol."""

    __slots__ = (
        "symbol", "signals", "last_price", "last_ts_ns", "session_start_ns",
        "realised_pnl", "open_notional", "open_side", "open_entry", "open_entry_ns", "trades_today",
        "consecutive_losses", "last_exit_ns", "halted", "halt_reason",
        "equity", "peak_equity", "tick_count", "_notes",
        "day_index", "day_start_pnl", "day_rollovers",
    )

    def __init__(self, symbol: str, equity: float = 100_000.0) -> None:
        self.symbol = symbol
        self.signals: dict[str, Signal] = {}
        self.last_price = 0.0
        self.last_ts_ns = 0
        self.session_start_ns = 0
        # Position / P&L state, maintained by the executor so every agent
        # sees the same book. Agents never write these.
        self.realised_pnl = 0.0
        self.open_notional = 0.0
        self.open_side = 0          # -1 short, 0 flat, +1 long
        self.open_entry = 0.0
        self.open_entry_ns = 0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.last_exit_ns = 0
        self.halted = False
        self.halt_reason = ""
        self.equity = equity
        self.peak_equity = equity
        self.tick_count = 0
        self._notes: list[str] = []
        # A 24/7 market has no session close, so "today" has to be derived
        # from the clock. Without this, a multi-day run sets session_start
        # once and the daily loss limit silently becomes a whole-run limit —
        # the first drawdown halts the system permanently.
        self.day_index = -1
        self.day_start_pnl = 0.0
        self.day_rollovers = 0

    # ---- signal plane -------------------------------------------------

    def publish(self, sig: Signal) -> None:
        """Last-value-wins. One atomic dict store; safe cross-thread."""
        self.signals[sig.agent] = sig

    def get(self, agent: str) -> Optional[Signal]:
        return self.signals.get(agent)

    def read(self, agent: str, now_ns: int) -> float:
        """An agent's *effective* opinion right now: strength scaled by its
        own confidence and by how stale it is. Dead signals read as exactly
        0.0, so a caller never has to check liveness itself."""
        s = self.signals.get(agent)
        if s is None:
            return 0.0
        f = s.freshness(now_ns)
        return 0.0 if f <= 0.0 else s.strength * s.confidence * f

    def live_signals(self, now_ns: int) -> list[Signal]:
        return [s for s in self.signals.values() if s.alive(now_ns)]

    # ---- book state ---------------------------------------------------

    @property
    def flat(self) -> bool:
        return self.open_side == 0

    @property
    def drawdown(self) -> float:
        """Current drawdown from peak equity, as a positive fraction."""
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - self.equity) / self.peak_equity
        return dd if dd > 0 else 0.0

    def unrealised(self, price: float) -> float:
        if self.open_side == 0 or self.open_entry <= 0:
            return 0.0
        qty = self.open_notional / self.open_entry
        return (price - self.open_entry) * qty * self.open_side

    def roll_day(self, ts_ns: int) -> bool:
        """Advance the trading day if the UTC date changed. Returns True on
        a rollover. Resets the per-day risk budget and clears a halt that
        was imposed for a per-day reason."""
        d = ts_ns // 86_400_000_000_000
        if d == self.day_index:
            return False
        first = self.day_index == -1
        self.day_index = d
        self.day_start_pnl = self.realised_pnl
        self.trades_today = 0
        self.session_start_ns = ts_ns
        if not first:
            self.day_rollovers += 1
            # A loss streak used to clear only on a winning trade. WARDEN
            # shrinks size during a streak, and on a small account the
            # shrunken size is below one contract, so no trade could be
            # placed, so no win could ever clear the streak. A new day
            # starts the count again.
            self.consecutive_losses = 0
            if self.halted and self.halt_reason.startswith("daily"):
                self.halted = False
                self.halt_reason = ""
        return not first

    @property
    def day_pnl(self) -> float:
        """Realised P&L since the start of the current UTC day."""
        return self.realised_pnl - self.day_start_pnl

    def mark(self, price: float, ts_ns: int) -> None:
        self.roll_day(ts_ns)
        self.last_price = price
        self.last_ts_ns = ts_ns
        self.tick_count += 1
        if self.session_start_ns == 0:
            self.session_start_ns = ts_ns
        eq = self.equity + self.unrealised(price)
        if eq > self.peak_equity:
            self.peak_equity = eq

    def seconds_since_exit(self, now_ns: int) -> float:
        if self.last_exit_ns == 0:
            return 1e9
        return (now_ns - self.last_exit_ns) / NS_PER_S

    def session_seconds(self, now_ns: int) -> float:
        if self.session_start_ns == 0:
            return 0.0
        return (now_ns - self.session_start_ns) / NS_PER_S

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def note(self, text: str) -> None:
        """Diagnostics only. Bounded so a long session cannot grow it
        without limit — an unbounded log list is a slow memory leak."""
        n = self._notes
        n.append(text)
        if len(n) > 500:
            del n[:250]

    @property
    def notes(self) -> list[str]:
        return list(self._notes)
