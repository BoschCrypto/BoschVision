"""ARBITER — the overseer. Decides LONG, SHORT or FLAT.

It is the only agent that produces an order intent, and it produces exactly
one view of the world by fusing the others. Its job is not to have an
opinion of its own; it is to decide whose opinion to believe right now.

How it fuses, and why it is not a simple weighted average:

  1. Each agent's vote is strength * confidence * freshness. An agent that
     is unsure, or whose read has aged, loses its say automatically.
  2. CONFLUENCE IS REQUIRED. A single agent, however confident, cannot
     open a position. At least `min_agreeing` independent agents must point
     the same way. This is the difference between a swarm and a committee
     that rubber-stamps whoever shouted first — and it is the main reason
     a multi-agent design beats a single strategy: uncorrelated confirmation.
  3. DISAGREEMENT SHRINKS SIZE, it does not get averaged away. Two agents
     pulling opposite ways is information — it says the setup is unclear —
     and averaging destroys exactly that information. The dispersion of
     votes scales the final size down.
  4. SENTINEL holds a VETO, not a vote weight. Strong news against the
     intended direction blocks the trade outright rather than being
     outvoted by three technical agents that cannot read.
  5. Hysteresis. Entry needs a higher score than exit. Without a band, a
     score oscillating around the threshold produces a stream of trades
     that pay the spread each time and are profitable for nobody but the
     broker.

Flip handling is explicit: a reversal is a close plus an open, is checked
against risk twice, and is never allowed to skip the cooldown — a bot that
can flip instantly will flip forever in chop.
"""
from __future__ import annotations

from typing import Optional

from ..bus import Blackboard
from ..types import NS_PER_S, Decision, Side, Signal, Tick
from .base import FastAgent
from .warden import Warden


class Arbiter(FastAgent):
    name = "ARBITER"

    __slots__ = (
        "weights", "entry_threshold", "exit_threshold", "min_agreeing",
        "news_veto_level", "ttl_ns", "_last_side", "_last_score",
        "max_dispersion", "min_hold_ns", "exit_persist", "_exit_streak",
    )

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
        entry_threshold: float = 0.28,
        exit_threshold: float = 0.10,
        min_agreeing: int = 2,
        news_veto_level: float = 0.55,
        max_dispersion: float = 0.9,
        min_hold_s: float = 30.0,
        exit_persist: int = 5,
        ttl_s: float = 5.0,
    ) -> None:
        # SENTINEL's weight is small by design — it votes a little and
        # vetoes a lot. See the module docstring.
        self.weights = weights or {"QUANT": 0.45, "CHARTIST": 0.40, "SENTINEL": 0.15}
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.min_agreeing = min_agreeing
        self.news_veto_level = news_veto_level
        self.max_dispersion = max_dispersion
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self.min_hold_ns = int(min_hold_s * NS_PER_S)
        self.exit_persist = exit_persist
        self._exit_streak = 0
        self._last_side = 0
        self._last_score = 0.0

    # The arbiter publishes its fused view as a signal too, so the
    # dashboard and journal can see what it thought even on FLAT ticks.
    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        score, agree, disp, _ = self._fuse(tick.ts_ns, bb)
        self._last_score = score
        conf = max(0.0, 1.0 - disp) * min(1.0, agree / max(1, self.min_agreeing))
        return Signal(self.name, tick.ts_ns, score, conf, self.ttl_ns,
                      f"score={score:+.2f} agree={agree} disp={disp:.2f}")

    # -- fusion -----------------------------------------------------------

    def _fuse(self, now_ns: int, bb: Blackboard) -> tuple[float, int, float, str]:
        """Returns (score, agreeing_count, dispersion, vote_detail)."""
        total = 0.0
        wsum = 0.0
        votes: list[tuple[str, float]] = []
        for agent, w in self.weights.items():
            v = bb.read(agent, now_ns)      # strength * conf * freshness
            if v == 0.0:
                continue
            total += w * v
            wsum += w
            votes.append((agent, v))

        if wsum <= 0 or not votes:
            return 0.0, 0, 1.0, ""

        score = total / wsum

        # Confluence: count only meaningful votes, not rounding noise.
        pos = sum(1 for _, v in votes if v > 0.05)
        neg = sum(1 for _, v in votes if v < -0.05)
        agree = pos if score > 0 else neg

        # Dispersion: mean absolute deviation of votes from the fused
        # score, normalised. 0 = unanimous, 1 = maximally split.
        mad = sum(abs(v - score) for _, v in votes) / len(votes)
        disp = min(1.0, mad / self.max_dispersion)

        detail = " ".join(f"{a}:{v:+.2f}" for a, v in votes)
        return score, agree, disp, detail

    @staticmethod
    def _entry_ns(bb: Blackboard) -> int:
        """When the open position was entered. Falls back to 'now' when the
        board has no open trade, which makes held_ns zero rather than
        enormous — a stale 0 here would let every position exit instantly."""
        return getattr(bb, "open_entry_ns", 0) or bb.last_ts_ns

    # -- the decision -----------------------------------------------------

    def decide(self, tick: Tick, bb: Blackboard, warden: Warden) -> Decision:
        now = tick.ts_ns
        px = tick.price
        score, agree, disp, detail = self._fuse(now, bb)
        self._last_score = score

        flat = Decision(now, bb.symbol, Side.FLAT, 0.0, px, score=score, votes=detail)

        # --- exit logic first: managing an open position outranks opening ---
        if bb.open_side != 0:
            cur = bb.open_side
            held_ns = now - self._entry_ns(bb)
            weak = (score * cur) < self.exit_threshold

            # Two guards against exiting on noise, both learned the hard
            # way from a first run that averaged 0.6s per trade and never
            # once reached a stop or a target:
            #
            #   persistence — the exit condition must hold for N ticks in
            #       a row. A single tick that dips below the band is the
            #       tape breathing, not a thesis breaking.
            #   minimum hold — below this, only the STOP may close the
            #       trade (stops live in the executor and are checked
            #       every tick regardless of this branch). Otherwise the
            #       system pays the spread to enter and exit inside a
            #       second, which is a transfer to the broker, not a trade.
            self._exit_streak = self._exit_streak + 1 if weak else 0

            # A hard flip (conviction now strongly the OTHER way) bypasses
            # the minimum hold: being wrong-way in size is worse than
            # paying a spread.
            hard_flip = (score * cur) < -self.entry_threshold

            if hard_flip or (self._exit_streak >= self.exit_persist
                             and held_ns >= self.min_hold_ns):
                self._exit_streak = 0
                why = "flip" if hard_flip else "decay"
                flat.reason = (
                    f"exit ({why}): score {score:+.2f} vs "
                    f"{'LONG' if cur > 0 else 'SHORT'} held {held_ns/NS_PER_S:.0f}s"
                )
                return flat
            flat.reason = (
                f"hold {'LONG' if cur > 0 else 'SHORT'} score={score:+.2f} "
                f"weak={self._exit_streak}/{self.exit_persist}"
            )
            return flat
        self._exit_streak = 0

        # --- entry ---------------------------------------------------------
        if abs(score) < self.entry_threshold:
            flat.reason = f"no edge: |{score:+.2f}| < {self.entry_threshold:.2f}"
            return flat

        if agree < self.min_agreeing:
            flat.reason = f"no confluence: {agree} agreeing, need {self.min_agreeing}"
            return flat

        side = Side.LONG if score > 0 else Side.SHORT
        want = 1 if score > 0 else -1

        # SENTINEL's veto: strong news pointing the other way.
        news = bb.get("SENTINEL")
        if news is not None and news.alive(now):
            eff = news.strength * news.confidence
            if abs(eff) >= self.news_veto_level and (eff * want) < 0:
                flat.reason = (
                    f"SENTINEL veto: news {eff:+.2f} opposes {side.name} "
                    f"— {news.reason[:50]}"
                )
                return flat

        verdict = warden.review(want, tick, bb)
        if not verdict.allow:
            flat.reason = f"WARDEN veto: {verdict.reason}"
            return flat

        # Disagreement shrinks size rather than being averaged away.
        size_mult = (1.0 - disp) * min(1.0, abs(score) / max(1e-9, self.entry_threshold * 2.0))
        size_mult = max(0.15, min(1.0, size_mult))
        # Round the haircut size to whole contracts. This used to be
        # `if notional < px`, the same price-is-not-granularity bug WARDEN
        # had: it blocked 7,081 otherwise-valid entries on ETH.
        #
        # On a small account the haircut often cannot be expressed: WARDEN
        # approved one contract and 0.6 of a contract does not exist. The
        # rule: if the haircut leaves less than one contract, take the one
        # contract WARDEN already approved only when conviction is at least
        # half strength; below that, a setup too unclear to deserve half
        # size does not get full size by default — it gets skipped.
        notional = warden.round_notional(verdict.max_notional * size_mult, px)
        if notional <= 0.0:
            if size_mult >= 0.5:
                notional = warden.round_notional(verdict.max_notional, px)
                unit = warden.one_unit(px)
                if notional > unit > 0.0:
                    notional = unit
            if notional <= 0.0:
                flat.reason = "below minimum size after dispersion haircut"
                return flat

        stop, target = warden.stop_and_target(want, px)
        self._last_side = want
        return Decision(
            now, bb.symbol, side, notional, px, stop, target, score,
            reason=f"{detail} | disp={disp:.2f} size x{size_mult:.2f} | {verdict.reason}",
            votes=detail,
        )
