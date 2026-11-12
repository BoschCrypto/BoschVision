"""Hot-path data types.

Everything here uses __slots__ and plain floats. No dicts, no pandas, no
numpy scalars — attribute access on a slotted object is roughly 3x faster
than a dict lookup and allocates nothing beyond the object itself, which
matters when this runs tens of thousands of times a second.

Timestamps are integer nanoseconds (`time.perf_counter_ns` domain for
latency, epoch-ns for market time). Floats lose resolution above ~2^53 ns;
ints never do.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Optional

NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000


class Side(IntEnum):
    """Signed so arithmetic works: strength * side is a directional score."""
    SHORT = -1
    FLAT = 0
    LONG = 1

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.name


class Tick:
    """One market observation. Trade print or quote update."""
    __slots__ = ("ts_ns", "symbol", "price", "size", "bid", "ask")

    def __init__(
        self,
        ts_ns: int,
        symbol: str,
        price: float,
        size: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
    ) -> None:
        self.ts_ns = ts_ns
        self.symbol = symbol
        self.price = price
        self.size = size
        # Bid/ask default to price so a trade-only feed still works; the
        # spread guard then reads 0, which it treats as "unknown", not "free".
        self.bid = bid or price
        self.ask = ask or price

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        mid = (self.ask + self.bid) * 0.5
        return 0.0 if mid <= 0 else (self.ask - self.bid) / mid * 10_000.0

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"Tick({self.symbol} {self.price:.4f} x{self.size:g})"


class Signal:
    """One agent's current opinion.

    strength   : -1.0 (max short conviction) .. +1.0 (max long conviction)
    confidence :  0.0 .. 1.0 — how much the agent trusts its own read right
                  now (low when its indicators are still warming up, when
                  the regime is hostile to its method, or when its inputs
                  are stale).
    ttl_ns     : after this long with no update the signal is dead, not
                 merely old. A news read from 40 minutes ago is not a weak
                 signal, it is no signal.
    """
    __slots__ = ("agent", "ts_ns", "strength", "confidence", "ttl_ns", "reason")

    def __init__(
        self,
        agent: str,
        ts_ns: int,
        strength: float,
        confidence: float,
        ttl_ns: int,
        reason: str = "",
    ) -> None:
        self.agent = agent
        self.ts_ns = ts_ns
        # Clamp at the boundary so no downstream maths has to defend itself.
        self.strength = -1.0 if strength < -1.0 else (1.0 if strength > 1.0 else strength)
        self.confidence = 0.0 if confidence < 0.0 else (1.0 if confidence > 1.0 else confidence)
        self.ttl_ns = ttl_ns
        self.reason = reason

    def age_ns(self, now_ns: int) -> int:
        return now_ns - self.ts_ns

    def alive(self, now_ns: int) -> bool:
        return (now_ns - self.ts_ns) < self.ttl_ns

    def freshness(self, now_ns: int) -> float:
        """1.0 at birth, decaying linearly to 0.0 at TTL.

        Linear, not exponential, on purpose: an exponential tail leaves a
        stale opinion with a small but nonzero vote forever, and "slightly
        counted forever" is how dead information poisons a fusion model.
        """
        if self.ttl_ns <= 0:
            return 0.0
        age = now_ns - self.ts_ns
        if age <= 0:
            return 1.0
        if age >= self.ttl_ns:
            return 0.0
        return 1.0 - (age / self.ttl_ns)

    @property
    def side(self) -> Side:
        if self.strength > 0:
            return Side.LONG
        if self.strength < 0:
            return Side.SHORT
        return Side.FLAT

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"Signal({self.agent} {self.strength:+.2f} "
            f"conf={self.confidence:.2f} {self.reason!r})"
        )


class Verdict:
    """WARDEN's answer. `allow=False` is absolute — nothing overrides it."""
    __slots__ = ("allow", "max_notional", "reason", "flatten")

    def __init__(
        self,
        allow: bool,
        max_notional: float = 0.0,
        reason: str = "",
        flatten: bool = False,
    ) -> None:
        self.allow = allow
        self.max_notional = max_notional
        self.reason = reason
        # flatten=True means "not only no new risk — close what is open".
        self.flatten = flatten

    def __repr__(self) -> str:  # pragma: no cover - display only
        verb = "ALLOW" if self.allow else "VETO"
        return f"Verdict({verb} ${self.max_notional:,.0f} {self.reason!r})"


class Decision:
    """ARBITER's output: a fully specified order intent, or FLAT."""
    __slots__ = (
        "ts_ns", "symbol", "side", "notional", "price",
        "stop", "target", "score", "reason", "votes",
    )

    def __init__(
        self,
        ts_ns: int,
        symbol: str,
        side: Side,
        notional: float,
        price: float,
        stop: float = 0.0,
        target: float = 0.0,
        score: float = 0.0,
        reason: str = "",
        votes: Optional[str] = None,
    ) -> None:
        self.ts_ns = ts_ns
        self.symbol = symbol
        self.side = side
        self.notional = notional
        self.price = price
        self.stop = stop
        self.target = target
        self.score = score
        self.reason = reason
        self.votes = votes or ""

    @property
    def is_trade(self) -> bool:
        return self.side is not Side.FLAT and self.notional > 0

    def __repr__(self) -> str:  # pragma: no cover - display only
        if not self.is_trade:
            return f"Decision(FLAT {self.reason!r})"
        return (
            f"Decision({self.side.name} {self.symbol} ${self.notional:,.0f} "
            f"@{self.price:.4f} stop={self.stop:.4f} score={self.score:+.2f})"
        )
