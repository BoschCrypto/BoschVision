"""A synthetic market, for testing the system at full speed.

This is NOT a backtest and must not be read as one. It generates plausible
microstructure — trend regimes, mean-reverting chop, volatility clustering,
news jumps, a quoted spread that widens with volatility, and lognormal
trade sizes — so that the agents, the risk gates and the latency profile
can be exercised deterministically and at millions of ticks.

What it proves: the system runs, the agents interact, the gates fire, the
loop is fast, the accounting balances.

What it cannot prove: that the strategy makes money. A synthetic series
contains exactly the structure its author put in, so measuring edge against
it is circular. Real data, out-of-sample, is the only thing that answers
that — see `velocity replay`.
"""
from __future__ import annotations

import random
from typing import Iterator, Optional

from ..types import NS_PER_MS, NS_PER_S, Tick


class SyntheticFeed:
    def __init__(
        self,
        symbol: str = "SYNTH",
        start_price: float = 100.0,
        n_ticks: int = 100_000,
        tick_interval_ms: float = 100.0,
        base_vol_bps: float = 4.0,
        regime_len: int = 2000,
        news_prob: float = 0.00005,
        drift_scale: float = 1.0,
        seed: Optional[int] = 42,
        start_ts_ns: int = 1_700_000_000 * NS_PER_S,
    ) -> None:
        self.symbol = symbol
        self.start_price = start_price
        self.n_ticks = n_ticks
        self.tick_interval_ms = tick_interval_ms
        self.base_vol_bps = base_vol_bps
        self.regime_len = regime_len
        self.news_prob = news_prob
        # drift_scale=0.0 produces a DRIFTLESS random walk: no trend regimes,
        # no exploitable structure. This is the null hypothesis. Any strategy
        # that "makes money" on it is reading its own noise, and any strategy
        # that loses exactly its costs on it is behaving correctly.
        self.drift_scale = drift_scale
        self.seed = seed
        self.start_ts_ns = start_ts_ns
        self.news_events: list[tuple[int, float]] = []

    def __iter__(self) -> Iterator[Tick]:
        rng = random.Random(self.seed)
        px = self.start_price
        ts = self.start_ts_ns
        step_ns = int(self.tick_interval_ms * NS_PER_MS)
        vol = self.base_vol_bps / 10_000.0
        # Volatility clustering: a slow-moving multiplier on the per-tick sd.
        vol_mult = 1.0
        drift = 0.0
        regime_left = 0
        self.news_events = []

        for i in range(self.n_ticks):
            if regime_left <= 0:
                regime_left = int(self.regime_len * rng.uniform(0.4, 1.8))
                r = rng.random()
                if r < 0.33:
                    drift = rng.uniform(0.15, 0.55) * vol * self.drift_scale
                elif r < 0.66:
                    drift = -rng.uniform(0.15, 0.55) * vol * self.drift_scale
                else:
                    drift = 0.0                                 # chop
            regime_left -= 1

            # GARCH-ish volatility persistence.
            vol_mult = 0.995 * vol_mult + 0.005 * rng.lognormvariate(0.0, 0.35)
            vol_mult = max(0.3, min(5.0, vol_mult))

            shock = 0.0
            if rng.random() < self.news_prob:
                shock = rng.choice([-1, 1]) * rng.uniform(15, 60) / 10_000.0
                self.news_events.append((i, shock))
                vol_mult = min(5.0, vol_mult * 2.5)

            ret = drift + rng.gauss(0.0, vol * vol_mult) + shock
            # Mild mean reversion in chop keeps the series from wandering
            # off to zero or infinity over long runs.
            if drift == 0.0 and self.drift_scale > 0:
                ret -= 0.02 * (px / self.start_price - 1.0)
            px *= (1.0 + ret)
            if px < 1.0:
                px = 1.0

            # Spread widens with volatility — the cost of trading rises
            # exactly when the signal looks most exciting.
            spread_bps = 1.0 + 2.5 * vol_mult
            half = px * spread_bps / 20_000.0
            size = rng.lognormvariate(4.0, 1.0)

            ts += step_ns
            yield Tick(ts, self.symbol, px, size, px - half, px + half)
