"""CHARTIST — reads the shape of the tape, not its statistics.

Where QUANT asks "what do the numbers say", CHARTIST asks the questions a
discretionary trader asks looking at a chart:

  * Where are the levels, and did we just take one out?
  * Is the range compressing (coiling) or expanding (resolved)?
  * Is structure intact — higher highs and higher lows, or the reverse?
  * Did the break come with participation, or is it an air pocket?
  * Where is price relative to VWAP, the day's reference line?

The part most chart logic gets wrong is the failed break. A break of the
high on falling participation, into an already-stretched tape, is the most
reliable SHORT setup on an intraday chart — it is where trapped buyers are.
So CHARTIST does not simply vote long on an up-break: it checks the break
for confirmation and, failing it, votes the other way with its reason
recorded as a trap.

Interaction: it reads QUANT's trend off the blackboard and treats a break
with the trend as continuation (full weight) and a break against it as
suspect (halved, and trap-checked harder).
"""
from __future__ import annotations

from typing import Optional

from ..bus import Blackboard
from ..indicators import EMA, VWAP, RollingExtrema, RollingStats, StreamATR
from ..types import NS_PER_S, Signal, Tick
from .base import FastAgent


class Chartist(FastAgent):
    name = "CHARTIST"

    __slots__ = (
        "ext_fast", "ext_slow", "atr", "vwap", "vol_ema", "range_stats",
        "_prev_px", "_swing_hi", "_swing_lo", "_structure", "ttl_ns",
        "_since_break", "_break_dir", "_day",
    )

    def __init__(
        self,
        fast_window: int = 60,
        slow_window: int = 300,
        atr_period: int = 20,
        vol_period: int = 60,
        ttl_s: float = 5.0,
    ) -> None:
        self.ext_fast = RollingExtrema(fast_window)
        self.ext_slow = RollingExtrema(slow_window)
        self.atr = StreamATR(atr_period)
        self.vwap = VWAP()
        self.vol_ema = EMA(vol_period)
        self.range_stats = RollingStats(slow_window)
        self._prev_px = 0.0
        self._swing_hi = 0.0
        self._swing_lo = 0.0
        self._structure = 0.0     # +1 higher highs/lows, -1 lower
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self._since_break = 10_000
        self._break_dir = 0
        self._day = -1

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        px = tick.price
        atr = self.atr.update(px)
        # VWAP is the DAY's reference line. On a market that never closes it
        # was never reset, so over a multi-day run price sat a hundred ATRs
        # from a weeks-old average and this term pinned itself at +/-1,
        # tilting every vote the same way.
        if bb.day_index != self._day:
            self._day = bb.day_index
            self.vwap.reset()
        vw = self.vwap.update(px, tick.size)
        vol_avg = self.vol_ema.update(tick.size)

        # Levels BEFORE this tick is folded in — otherwise price always
        # equals its own rolling max and nothing ever "breaks out". This
        # off-by-one is the single most common bug in breakout code.
        prev_hi = self.ext_fast.max
        prev_lo = self.ext_fast.min
        ready = self.ext_slow.ready and self.atr.ready

        self.ext_fast.update(px)
        self.ext_slow.update(px)
        self.range_stats.update(px)
        self._since_break += 1

        if not ready:
            self._prev_px = px
            return Signal(self.name, tick.ts_ns, 0.0, 0.0, self.ttl_ns, "warming up")

        unit = atr if atr > px * 1e-4 else px * 1e-4

        # --- structure: track swings in ATR units -------------------------
        if px > self._swing_hi or self._swing_hi == 0.0:
            if px > self._swing_hi:
                self._structure = 0.7 * self._structure + 0.3
            self._swing_hi = px
            if self._swing_lo == 0.0:
                self._swing_lo = px
        elif px < self._swing_lo or self._swing_lo == 0.0:
            if px < self._swing_lo:
                self._structure = 0.7 * self._structure - 0.3
            self._swing_lo = px
        # Decay the swing anchors toward price so stale extremes from an
        # hour ago stop defining "structure" for the rest of the session.
        self._swing_hi += (px - self._swing_hi) * 0.0005
        self._swing_lo += (px - self._swing_lo) * 0.0005
        if self._structure > 1.0:
            self._structure = 1.0
        elif self._structure < -1.0:
            self._structure = -1.0

        # --- compression: narrow slow range = coiled ----------------------
        slow_range = self.ext_slow.range
        compression = 0.0
        if slow_range > 0:
            ratio = slow_range / unit
            # < 6 ATR of range over the slow window is tight; > 20 is wide.
            compression = max(0.0, min(1.0, (14.0 - ratio) / 10.0))

        # --- the break ----------------------------------------------------
        trend = bb.read("QUANT", tick.ts_ns)
        participation = (tick.size / vol_avg) if vol_avg > 1e-9 else 1.0
        broke_up = prev_hi > 0 and px > prev_hi and self._prev_px <= prev_hi
        broke_dn = prev_lo > 0 and px < prev_lo and self._prev_px >= prev_lo

        break_score = 0.0
        trap = False
        if broke_up or broke_dn:
            self._since_break = 0
            self._break_dir = 1 if broke_up else -1
            direction = 1.0 if broke_up else -1.0
            # Confirmation: participation above average, and the break is
            # not happening into an already-exhausted slow range.
            extension = (px - self.ext_slow.min) / slow_range if slow_range > 0 else 0.5
            exhausted = extension > 0.97 if broke_up else extension < 0.03
            confirmed = participation >= 1.2 and not exhausted
            with_trend = (trend * direction) > 0.02
            if confirmed:
                break_score = direction * (0.55 + 0.25 * min(1.0, participation / 3.0))
                if not with_trend:
                    break_score *= 0.5     # counter-trend break: suspect
            else:
                # Unconfirmed break = trap. Fade it, at reduced size.
                break_score = -direction * 0.40
                trap = True

        # A break's influence decays over the ticks that follow it, so the
        # signal reflects "we broke out just now", not "we broke out once".
        if self._since_break < 40:
            decay = 1.0 - (self._since_break / 40.0)
            break_score *= decay if break_score else 0.0
        else:
            break_score = 0.0

        # --- VWAP location ------------------------------------------------
        vwap_dev = (px - vw) / unit if vw > 0 else 0.0
        vwap_term = vwap_dev / (1.0 + abs(vwap_dev))

        strength = (
            0.45 * break_score
            + 0.30 * self._structure
            + 0.25 * vwap_term
        )
        # Coiled-and-quiet is a setup, not a trade. Compression scales the
        # read DOWN until something actually breaks.
        if self._since_break >= 40:
            strength *= (1.0 - 0.5 * compression)

        conf = 0.25 + 0.45 * min(1.0, abs(self._structure)) + 0.30 * (1.0 if self._since_break < 40 else 0.0)
        if trap:
            conf *= 0.75     # trap fades are real but lower-hit-rate
        if abs(strength) < 0.06:
            conf *= 0.5

        self._prev_px = px
        reason = (
            f"struct={self._structure:+.2f} brk={break_score:+.2f}"
            f"{' TRAP' if trap else ''} vwap={vwap_dev:+.2f}atr comp={compression:.2f}"
        )
        return Signal(self.name, tick.ts_ns, strength, conf, self.ttl_ns, reason)
