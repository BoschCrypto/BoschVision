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

import re
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


_VETO_NUM = re.compile(r"[-+]?[\d,]*\.?\d+x?")


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
        # Benchmark state: buy-and-hold of the same instrument over the same
        # window. ROADMAP.md made "always report a benchmark" the standard
        # for this repo and VELOCITY was violating it.
        self.first_price = 0.0
        self.bars_in_market = 0
        self._prev_ts_ns = 0
        self._bar_s = 0.0             # smoothed seconds between ticks
        # Watch-only: the agents run and decide, but no position is ever
        # opened. The default for the live view, because no strategy in this
        # repository has shown an edge (see FINDINGS.md).
        self.watch_only = False
        self._last_decision: Optional[Decision] = None

    # -- the hot path -----------------------------------------------------

    def on_tick(self, tick: Tick) -> Optional[Decision]:
        t0 = time.perf_counter_ns()
        bb = self.bb
        if self.first_price == 0.0:
            self.first_price = tick.price
        if bb.open_side != 0:
            self.bars_in_market += 1
        bb.mark(tick.price, tick.ts_ns)

        # Volatility per SECOND, for the fill model's latency drift. ATR is
        # the typical move over one bar; the executor asks how far price
        # travels during a 100 ms round trip. Passing the per-bar figure
        # straight through, as this did, charged a day's move for a tenth of
        # a second on daily bars (about 40 bps a fill) and overstated costs
        # on one-minute bars by roughly 5%.
        dt_s = (tick.ts_ns - self._prev_ts_ns) / NS_PER_S if self._prev_ts_ns else 0.0
        self._prev_ts_ns = tick.ts_ns
        if dt_s > 0:
            self._bar_s += (dt_s - self._bar_s) * (0.2 if self._bar_s else 1.0)
        vol_unit = (self.warden.atr_value or tick.price * 1e-4) / max(self._bar_s, 1e-3) ** 0.5

        # 2. the open trade is managed before anything else thinks.
        self.executor.manage(tick, bb, vol_unit)
        if not bb.flat:
            why = self.warden.must_flatten(tick, bb)
            if why:
                self.executor.close(tick, bb, f"risk flatten: {why}", vol_unit)
                self.vetoes["flattened by risk"] = self.vetoes.get("flattened by risk", 0) + 1

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
        if d.is_trade and bb.flat and not self.watch_only:
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
            # Bucket by reason TYPE, stripping varying numbers. Keying on
            # the full text gave 200+ entries like "vol spike 3.1x normal"
            # / "3.2x normal"; keying on just "WARDEN veto" was
            # undiagnosable. Strip the digits, keep the class.
            r = d.reason.split(":", 1)[-1].strip()
            key = _VETO_NUM.sub("#", r)[:60]
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

    def run(self, ticks: Iterable[Tick], max_ticks: Optional[int] = None,
            observer: Optional[Callable[[Tick, Decision], None]] = None) -> dict:
        """Drive the engine over a feed. `observer(tick, decision)` is called
        after every tick; it is how the recorder watches a run without the
        hot path knowing it is being watched."""
        self.start_slow_lane()
        n = 0
        try:
            for tick in ticks:
                d = self.on_tick(tick)
                if observer is not None:
                    observer(tick, d)
                n += 1
                if max_ticks and n >= max_ticks:
                    break
        finally:
            # Flat by the end, always. An open simulated position is an
            # unreported loss waiting to be discovered.
            if not self.bb.flat and self.bb.last_price > 0:
                last = Tick(self.bb.last_ts_ns, self.bb.symbol, self.bb.last_price)
                self.executor.close(last, self.bb, "end of session",
                                    (self.warden.atr_value or self.bb.last_price * 1e-4)
                                    / max(self._bar_s, 1e-3) ** 0.5)
            self.stop_slow_lane()
        return self.report(n)

    def significance(self) -> dict:
        """Is the result distinguishable from zero?

        A one-sample t-test on per-trade P&L. This does NOT prove edge — it
        only says whether the mean is far enough from zero, given the
        scatter and the sample size, to be worth a second look. With fewer
        than ~30 trades the answer is almost always no, and a strategy with
        a 2.0 t-stat found after trying twenty variants is still noise
        (that is multiple-testing, which no t-stat can see).
        """
        pnls = [t.pnl for t in self.executor.trades]
        n = len(pnls)
        if n < 2:
            return {"n": n, "t_stat": 0.0, "verdict": "too few trades"}
        mean = sum(pnls) / n
        var = sum((x - mean) ** 2 for x in pnls) / (n - 1)
        sd = var ** 0.5
        if sd <= 0:
            return {"n": n, "t_stat": 0.0, "verdict": "zero variance"}
        t = mean / (sd / (n ** 0.5))
        if n < 30:
            v = "SAMPLE TOO SMALL — cannot separate skill from luck"
        elif t <= -2.0:
            v = "significantly NEGATIVE — a reliable loser as configured"
        elif abs(t) < 1.0:
            v = "indistinguishable from zero"
        elif abs(t) < 2.0:
            v = "weak, not significant at 5%"
        elif abs(t) < 3.0:
            v = "significant at 5% — treat with suspicion, check for overfitting"
        else:
            v = "significant at 1% — still check out-of-sample"
        return {"n": n, "mean": mean, "sd": sd, "t_stat": t, "verdict": v}

    def benchmark(self, ticks_seen: int) -> dict:
        """Buy-and-hold the same instrument, same window, same capital."""
        if self.first_price <= 0 or self.bb.last_price <= 0:
            return {}
        bh = (self.bb.last_price / self.first_price - 1.0) * 100.0
        strat = (self.bb.equity / self.warden.start_equity - 1.0) * 100.0
        return {
            "buy_hold_pct": bh,
            "strategy_pct": strat,
            "excess_pct": strat - bh,
            "time_in_market_pct": (self.bars_in_market / ticks_seen * 100.0) if ticks_seen else 0.0,
        }

    def report(self, ticks_seen: int) -> dict:
        st = self.executor.stats()
        lat = self.latency.summary()
        bb = self.bb
        return {
            "benchmark": self.benchmark(ticks_seen),
            "significance": self.significance(),
            "day_rollovers": bb.day_rollovers,
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
