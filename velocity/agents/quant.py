"""QUANT — streaming technical analysis.

Four orthogonal reads, fused into one strength:

  1. Trend      fast EMA vs slow EMA, normalised by ATR so the same number
                means the same thing on a $3 stock and a $300 one.
  2. Momentum   rate of change over a short lookback, ATR-normalised.
  3. Stretch    z-score of price against its own recent distribution. Enters
                the fusion with a NEGATIVE weight: stretched price is a
                reason to fade, not to chase.
  4. RSI        confirmation only, and only at the extremes. Mid-range RSI
                carries no information and is deliberately ignored rather
                than scaled — scaling noise produces confident noise.

Interaction: QUANT reads SENTINEL off the blackboard. When a headline is
live, it drops the mean-reversion term's weight, because news-driven moves
continue far more often than they revert. That is the single most expensive
mistake a mean-reversion scalper makes — fading a move that had a reason.

Confidence, not strength, carries the uncertainty: a half-warmed indicator
set or a dead-flat tape yields a confident-looking strength with near-zero
confidence, and ARBITER's weighting makes that nearly irrelevant.
"""
from __future__ import annotations

from typing import Optional

from ..bus import Blackboard
from ..indicators import EMA, RollingStats, StreamATR, StreamRSI
from ..types import NS_PER_S, Signal, Tick
from .base import FastAgent


class Quant(FastAgent):
    name = "QUANT"

    __slots__ = (
        "fast", "slow", "atr", "rsi", "stats", "_roc_buf", "_roc_n", "_roc_i",
        "ttl_ns", "_last_strength",
    )

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 48,
        atr_period: int = 20,
        rsi_period: int = 14,
        z_window: int = 120,
        roc_lookback: int = 30,
        ttl_s: float = 5.0,
    ) -> None:
        self.fast = EMA(fast_period)
        self.slow = EMA(slow_period)
        self.atr = StreamATR(atr_period)
        self.rsi = StreamRSI(rsi_period)
        self.stats = RollingStats(z_window)
        # Preallocated ring for rate-of-change; never grows, never allocates.
        self._roc_n = roc_lookback
        self._roc_buf = [0.0] * roc_lookback
        self._roc_i = 0
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self._last_strength = 0.0

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        px = tick.price
        f = self.fast.update(px)
        s = self.slow.update(px)
        atr = self.atr.update(px)
        rsi = self.rsi.update(px)
        self.stats.update(px)

        i = self._roc_i
        prev = self._roc_buf[i]
        self._roc_buf[i] = px
        self._roc_i = (i + 1) % self._roc_n

        # Warming up: emit an explicit zero-confidence signal rather than
        # None, so the board shows QUANT as present-but-unready instead of
        # silently absent (which reads identically to "agent crashed").
        if not (self.slow.ready and self.atr.ready and self.stats.ready):
            return Signal(self.name, tick.ts_ns, 0.0, 0.0, self.ttl_ns, "warming up")

        # ATR floor: a dead-flat tape makes every denominator tiny and every
        # ratio enormous. 1bp of price is the floor below which we call the
        # move unmeasurable rather than infinite.
        unit = atr if atr > px * 1e-4 else px * 1e-4

        trend = (f - s) / unit
        roc = ((px - prev) / unit) if prev > 0 else 0.0
        z = self.stats.z(px)

        # Squash each term into [-1, 1] with a soft saturation. tanh would
        # be the textbook choice; x/(1+|x|) is ~4x cheaper and has the same
        # shape where it matters. In a hot loop that difference is real.
        t_trend = trend / (1.0 + abs(trend))
        t_roc = roc / (1.0 + abs(roc))
        t_z = z / (1.0 + abs(z))

        if rsi >= 70.0:
            t_rsi = -(rsi - 70.0) / 30.0
        elif rsi <= 30.0:
            t_rsi = (30.0 - rsi) / 30.0
        else:
            t_rsi = 0.0

        # News interaction: headline live -> fade terms get cut.
        news = bb.read("SENTINEL", tick.ts_ns)
        revert_w = 0.30 * (1.0 - 0.75 * min(1.0, abs(news) * 2.0))

        strength = (
            0.42 * t_trend
            + 0.28 * t_roc
            - revert_w * t_z
            + 0.12 * t_rsi
        )

        # Confidence: how much structure is actually present. A trend that
        # is a fraction of one ATR is noise however clean the maths looks.
        sep = abs(trend)
        conf = sep / (0.8 + sep)
        if self.stats.std < px * 1e-5:
            conf *= 0.2          # flat tape: nothing to trade
        if abs(strength) < 0.08:
            conf *= 0.5          # near-zero reads are not convictions

        self._last_strength = strength
        reason = f"trend={trend:+.2f}atr roc={roc:+.2f} z={z:+.2f} rsi={rsi:.0f}"
        return Signal(self.name, tick.ts_ns, strength, conf, self.ttl_ns, reason)
