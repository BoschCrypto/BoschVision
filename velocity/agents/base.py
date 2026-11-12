"""Agent contracts.

A FastAgent runs inside the tick loop. Its `on_tick` is called for every
tick and is held to a hard budget: no I/O, no logging, no allocation beyond
a single Signal, no try/except around normal flow. If a fast agent needs
something slow, it reads it off the blackboard where a slow agent left it.

A SlowAgent runs on its own thread at its own cadence. It may call the
network and models. It publishes to the same blackboard. It is never
awaited by the tick loop, so a hung API call degrades the system to
"trading without news" instead of stopping it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..bus import Blackboard
from ..types import Signal, Tick


class Agent(ABC):
    name: str = "AGENT"
    lane: str = "fast"


class FastAgent(Agent):
    lane = "fast"

    @abstractmethod
    def on_tick(self, tick: Tick, bb: Blackboard) -> Optional[Signal]:
        """Return this agent's current opinion, or None to leave its last
        opinion standing (it will decay on its own via TTL)."""
        ...

    def reset(self) -> None:  # pragma: no cover - default no-op
        """Called at session boundaries."""
        return None


class SlowAgent(Agent):
    lane = "slow"

    #: seconds between polls
    interval_s: float = 15.0

    @abstractmethod
    def poll(self, bb: Blackboard) -> Optional[Signal]:
        """Called on a background thread. May block on I/O."""
        ...
