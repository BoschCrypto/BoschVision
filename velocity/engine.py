"""The engine — the hot loop, and the slow-lane supervisor.

Hot loop order per tick, and the order matters:

    1. mark the book          (prices, equity, drawdown)
    2. manage the open trade  (stop/target BEFORE any new thinking)
    3. fast agents update     (QUANT, CHARTIST, WARDEN publish)
    4. ARBITER fuses          (one decision)
    5. WARDEN gates           (inside decide(); veto is final)
    6. execute                (open / close)

Step 2 is before step 3 deliberately. If a stop is hit, that is settled
before any agent gets to form an opinion about the tick that hit it —
otherwise a strong signal can talk the system out of honouring its own
stop, which is the exact failure that turns a small loss into a large one.

Latency is measured, not assumed: every tick is timed with
`perf_counter_ns` and the engine reports p50/p99/max at the end. If you
change an agent and the p99 moves, you will see it.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional

from .agents.arbiter import Arbiter
from .agents.base import FastAgent, SlowAgent
from .agents.chartist import Chartist
from .agents.quant import Quant
from .agents.sentinel import Sentinel
from .agents.warden import RiskLimits, Warden
from .bus import Blackboard
from .execution import ExecConfig, Fill, PaperExecutor
from .types import NS_PER_MS, NS_PER_S, Decision, Side, Tick


class LatencyRecorder:
    """Fixed-size reservoir of tick latencies. Preallocated: recording a
    sample must not allocate, or the measurement changes what it measures."""

    __slots__ = ("buf", "n", "cap", "total", "max_ns")

    def __init__(self, cap: int = 100_000) -> None:
        self.cap = cap
        self.buf = [0] * cap
        self.n = 0
        self.total = 0
        self.max_ns = 0

    def record(self, ns: int) -> None:
        i = self.n
        if i < self.cap:
            self.buf[i] = ns
        else:
            # Reservoir-free ring: keep the most recent `cap` samples.
            self.buf[i % self.cap] = ns
        self.n = i + 1
        self.total += ns
        if ns > self.max_ns:
            self.max_ns = ns

    def summary(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        k = min(self.n, self.cap)
        s = sorted(self.buf[:k])
        def pct(p: float) -> int:
            idx = int(p * (k - 1))
            return s[idx]
        return {
            "n": self.n,
            "mean_ns": self.total / self.n,
            "p50_ns": pct(0.50),
            "p90_ns": pct(0.90),
            "p99_ns": pct(0.99),
            "p999_ns": pct(0.999),
            "max_ns": self.max_ns,
        }


class SlowLane:
    """Runs SlowAgents on background threads. Never joined by the hot loop."""

    def __init__(self, agents: list[SlowAgent], bb: Blackboard) -> None:
        self.agents = agents
        self.bb = bb
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.errors: list[str] = []

    def start(self) -> None:
        for a in self.agents:
            t = threading.Thread(target=self._run, args=(a,), daemon=True,
                                 name=f"velocity-{a.name}")
            t.start()
            self._threads.append(t)

    def _run(self, agent: SlowAgent) -> None:
        while not self._stop.is_set():
            try:
                sig = agent.poll(self.bb)
                if sig is not None:
                    self.bb.publish(sig)
            except Exception as e:
                # A slow agent must never take the session down. Record and
                # keep polling; its signal ages out on its own if it stays
                # broken, and the fast lane degrades to price-only.
                msg = f"{agent.name}: {type(e).__name__}: {e}"
                self.errors.append(msg)
                if len(self.errors) > 100:
                    del self.errors[:50]
            self._stop.wait(agent.interval_s)

    def stop(self) -> None:
        self._stop.set()


class Engine:
    def __init__(
        self,
        symbol: str,
        equity: float = 100_000.0,
        limits: Optional[RiskLimits] = None,
        exec_cfg: Optional[ExecConfig] = None,
        quant: Optional[Quant] = None,
        chartist: Optional[Chartist] = None,
        arbiter: Optional[Arbiter] = None,
        sentinel: Optional[Sentinel] = None,
        on_fill: Optional[Callable[[Fill], None]] = None,
        on_decision: Optional[Callable[[Decision], None]] = None,
    ) -> None:
        self.bb = Blackboard(symbol, equity)
        self.quant = quant or Quant()
        self.chartist = chartist or Chartist()
        self.warden = Warden(limits, start_equity=equity)
        self.arbiter = arbiter or Arbiter()
        self.sentinel = sentinel
        # Order is the evaluation order inside the loop.
        self.fast: list[FastAgent] = [self.quant, self.chartist, self.warden, self.arbiter]
        self.executor = PaperExecutor(exec_cfg, on_fill=on_fill)
        self.on_decision = on_decision
        self.latency = LatencyRecorder()
        self.slow: Optional[SlowLane] = None
        self.decisions = 0
        self.vetoes: dict[str, int] = {}
        self._last_decision: Optional[Decision] = None

    # -- the hot path -----------------------------------------------------

    def on_tick(self, tick: Tick) -> Optional[Decision]:
        t0 = time.perf_counter_ns()
        bb = self.bb
        bb.mark(tick.price, tick.ts_ns)

        vol_unit = self.warden.atr_value or tick.price * 1e-4

        # 2. the open trade is managed before anything else thinks.
        self.executor.manage(tick, bb, vol_unit)

        # 3. fast agents publish.
        for a in self.fast:
            if a is self.arbiter:
                continue
            sig = a.on_tick(tick, bb)
            if sig is not None:
                bb.publish(sig)

        # 4+5. arbiter fuses and warden gates inside decide().
        d = self.arbiter.decide(tick, bb, self.warden)
        self.arbiter_signal_publish(tick)

        # 6. act.
        if d.is_trade and bb.flat:
            self.executor.open(d, tick, bb, vol_unit)
            self.warden.record_trade(tick.ts_ns)
            self.decisions += 1
            if self.on_decision:
                self.on_decision(d)
        elif not d.is_trade and not bb.flat and d.reason.startswith("exit"):
            self.executor.close(tick, bb, d.reason, vol_unit)
            self.decisions += 1
            if self.on_decision:
                self.on_decision(d)
        elif d.reason.startswith(("WARDEN veto", "SENTINEL veto")):
            key = d.reason.split(":")[0]
            self.vetoes[key] = self.vetoes.get(key, 0) + 1
            # A flatten order outranks everything, including an open trade.
            if not bb.flat and bb.halted:
                self.executor.close(tick, bb, "risk flatten", vol_unit)

        self._last_decision = d
        self.latency.record(time.perf_counter_ns() - t0)
        return d

    def arbiter_signal_publish(self, tick: Tick) -> None:
        sig = self.arbiter.on_tick(tick, self.bb)
        if sig is not None:
            self.bb.publish(sig)

    # -- driving ----------------------------------------------------------

    def start_slow_lane(self) -> None:
        if self.sentinel is not None and self.slow is None:
            self.slow = SlowLane([self.sentinel], self.bb)
            self.slow.start()

    def stop_slow_lane(self) -> None:
        if self.slow is not None:
            self.slow.stop()
            self.slow = None

    def run(self, ticks: Iterable[Tick], max_ticks: Optional[int] = None) -> dict:
        self.start_slow_lane()
        n = 0
        try:
            for tick in ticks:
                self.on_tick(tick)
                n += 1
                if max_ticks and n >= max_ticks:
                    break
        finally:
            # Flat by the end, always. An open simulated position is an
            # unreported loss waiting to be discovered.
            if not self.bb.flat and self.bb.last_price > 0:
                last = Tick(self.bb.last_ts_ns, self.bb.symbol, self.bb.last_price)
                self.executor.close(last, self.bb, "end of session",
                                    self.warden.atr_value or self.bb.last_price * 1e-4)
            self.stop_slow_lane()
        return self.report(n)

    def report(self, ticks_seen: int) -> dict:
        st = self.executor.stats()
        lat = self.latency.summary()
        bb = self.bb
        return {
            "symbol": bb.symbol,
            "ticks": ticks_seen,
            "equity": bb.equity,
            "start_equity": self.warden.start_equity,
            "return_pct": (bb.equity / self.warden.start_equity - 1.0) * 100.0,
            "max_drawdown_pct": bb.drawdown * 100.0,
            "halted": bb.halted,
            "halt_reason": bb.halt_reason,
            "vetoes": dict(self.vetoes),
            "slow_lane_errors": list(self.slow.errors) if self.slow else [],
            **st,
            "latency": lat,
        }
