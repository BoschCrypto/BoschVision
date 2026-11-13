"""CHARTIST as a channel-breakout rule.

    long   when a bar closes at or above the highest close of the previous N bars
    flat   when a bar closes at or below the lowest close of the previous N/2 bars

This is rule D from velocity/research (PLAN.md), unchanged, at whatever bar
length the feed delivers. It is the whole trading decision: ARBITER follows
this agent's state and nothing else gets a vote.

What the research found, so nobody reads the dashboard without it: on daily
bars this rule returned the most of everything tested and behaved like crash
insurance. On bars shorter than a day it lost money in every test, because
the average trade earns nothing before costs and it trades hundreds of times
a year (velocity/research/RESULTS3.md).
"""
from __future__ import annotations

from typing import Optional

from ..bus import Blackboard
from ..indicators import RollingExtrema
from ..types import NS_PER_S, Signal, Tick
from .base import FastAgent


class ChannelChartist(FastAgent):
    name = "CHARTIST"
    exit_label = "price made a new channel low"

    __slots__ = ("n", "exit_n", "_hi", "_lo", "_count", "state", "ttl_ns",
                 "channel_high", "channel_low", "_since_break", "_break_dir", "last_event")

    def __init__(self, n: int = 20, ttl_s: float = 5.0) -> None:
        if n < 4:
            raise ValueError("channel length must be at least 4 bars")
        self.n = n
        self.exit_n = max(2, n // 2)
        self._hi = RollingExtrema(n)
        self._lo = RollingExtrema(self.exit_n)
        self._count = 0
        self.state = 0
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self.channel_high = 0.0        # a close at or above this turns it long
        self.channel_low = 0.0         # a close at or below this turns it flat
        self._since_break = 10_000
        self._break_dir = 0
        self.last_event: Optional[tuple[str, str]] = None

    def reset(self) -> None:
        self.__init__(self.n)  # type: ignore[misc]

    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        px = tick.price
        self.last_event = None
        self._since_break += 1
        ready = self._count >= self.n
        if ready:
            # Levels are from the bars BEFORE this one. Folding this close in
            # first would make price equal its own high on every new high.
            hi, lo = self._hi.max, self._lo.min
            if px >= hi:
                if self.state == 0:
                    self.last_event = ("break", f"CHARTIST: close {px:,.2f} is a new {self.n}-bar high. Go long")
                    self._since_break, self._break_dir = 0, 1
                self.state = 1
            elif px <= lo:
                if self.state == 1:
                    self.last_event = ("trap", f"CHARTIST: close {px:,.2f} is a new {self.exit_n}-bar low. Go flat")
                    self._since_break, self._break_dir = 0, -1
                self.state = 0
        self._hi.update(px)
        self._lo.update(px)
        self._count += 1
        self.channel_high, self.channel_low = self._hi.max, self._lo.min
        if not ready:
            return Signal(self.name, tick.ts_ns, 0.0, 0.0, self.ttl_ns, "warming up")
        return Signal(self.name, tick.ts_ns, float(self.state), 1.0, self.ttl_ns,
                      f"{'LONG' if self.state else 'FLAT'} buy>={self.channel_high:.2f} sell<={self.channel_low:.2f}")
