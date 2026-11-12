"""VELOCITY — a multi-agent short-term trading system.

Separate from `hf_trading_bot` by design: different cadence (ticks, not daily
bars), different risk model (intraday, flat-by-close), different architecture
(a cooperating agent swarm, not a single strategy function).

Two lanes, because physics:

    FAST LANE  (hot path, target p99 < 1 ms, no allocation, no I/O)
        QUANT     — streaming technical analysis
        CHARTIST  — price structure / microstructure
        WARDEN    — risk gate, hard veto authority
        ARBITER   — fuses the above, decides LONG / SHORT / FLAT

    SLOW LANE  (off the hot path, seconds, may call models and the network)
        SENTINEL  — news and event scanning

SENTINEL never blocks a trade. It writes a decaying bias scalar onto the
blackboard; the fast lane reads it as a float. That is the only way to have
both "an agent that reads the news" and "a bot that reacts in microseconds".
"""
__version__ = "0.1.0"

from .types import Side, Signal, Tick, Decision, Verdict  # noqa: F401
