"""The loop that keeps the six agents working around the clock.

Every few seconds it asks each agent "are you due?" — its cadence elapsed, the
owner pressed Run now, or a task is waiting for it — and hands due agents to a
small worker pool. State lives in SQLite, so a restart picks up where it left
off instead of re-running everything at once.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .agents import AGENTS, APPROVAL_KINDS, Agent, Context, RunResult
from .llm import BudgetExceeded

log = logging.getLogger(__name__)

# Minutes after the very first start before each agent's first run, so a new
# install comes alive in a sensible order instead of all at once.
FIRST_RUN_DELAY = {"security": 0, "analytics": 0.25, "sales": 0.5, "marketing": 1,
                   "ads": 2, "delivery": 3}


def register_agents(store, agents: Optional[dict[str, Agent]] = None) -> None:
    """Create each agent's state row if missing. HQ's buttons write to these
    rows, so the web server calls this too (it may run with --no-agents)."""
    now = time.time()
    for key in agents or AGENTS:
        store.ensure_agent(key, now + 60 * FIRST_RUN_DELAY.get(key, 1))


class Scheduler:
    def __init__(self, ctx: Context, agents: Optional[dict[str, Agent]] = None,
                 tick_seconds: float = 5.0, workers: int = 2):
        self.ctx = ctx
        self.agents = agents or AGENTS
        self.tick_seconds = tick_seconds
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent")
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        register_agents(ctx.store, self.agents)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        # Only the long-running loop may declare earlier runs dead; a one-off
        # `agency run` next to a live `agency up` must not.
        self.ctx.store.recover_interrupted()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # the loop must outlive any one bad tick
                log.exception("scheduler tick failed")
            self._stop.wait(self.tick_seconds)

    # -- scheduling --------------------------------------------------------
    def due(self, now: Optional[float] = None) -> list[str]:
        now = now if now is not None else time.time()
        states = self.ctx.store.agent_states()
        queued = self.ctx.store.agents_with_queued_tasks()
        out = []
        for key in self.agents:
            st = states.get(key, {})
            scheduled = st.get("enabled") and now >= (st.get("next_run_ts") or 0)
            if scheduled or st.get("requested") or key in queued:
                out.append(key)
        return out

    def tick(self, now: Optional[float] = None) -> None:
        for key in self.due(now):
            with self._lock:
                if key in self._running:
                    continue
                self._running.add(key)
            self._pool.submit(self._run_and_release, key)

    def _run_and_release(self, key: str) -> None:
        try:
            self.run_agent(key)
        finally:
            with self._lock:
                self._running.discard(key)

    # -- one run -----------------------------------------------------------
    def run_agent(self, key: str, trigger: str = "schedule") -> RunResult:
        """Run one agent now, synchronously, and persist everything it made."""
        store, agent = self.ctx.store, self.agents[key]
        state = store.get_agent(key)
        task = store.claim_task(key)
        if task:
            trigger = "task"
        elif state.get("requested"):
            trigger = "manual"
        run_id = store.start_run(key, trigger, self.ctx.llm.backend)
        store.set_agent(key, status="running", requested=0)
        error = ""
        try:
            result = agent.run(self.ctx, task["instruction"] if task else None)
        except BudgetExceeded as ex:
            result = RunResult(f"Paused: {ex}", status="skipped")
        except Exception as ex:
            log.exception("agent %s failed", key)
            error = f"{type(ex).__name__}: {ex}"
            result = RunResult("Run failed", status="error")
        self._persist(agent, run_id, result)
        store.finish_run(run_id, result.status, result.summary, error)
        now = time.time()
        store.set_agent(key, status="error" if result.status == "error" else "idle",
                        last_run_ts=now, next_run_ts=now + 60 * agent.every_minutes(self.ctx.business),
                        last_summary=result.summary, last_error=error,
                        runs=(state.get("runs") or 0) + 1)
        if task:
            store.finish_task(task["id"], "failed" if result.status == "error" else "done", run_id)
        return result

    def _persist(self, agent: Agent, run_id: int, result: RunResult) -> None:
        store = self.ctx.store
        for item in result.items:
            status = "pending" if item["kind"] in APPROVAL_KINDS else "info"
            store.add_work(agent.key, run_id, item["kind"], item["title"], item["body"],
                           status, item.get("lead_id"))
            if item["kind"] == "alert":
                self.ctx.notify(f"{agent.codename}: {item['title']}", item["body"][:500])
        if result.findings:
            store.add_findings(run_id, result.findings)
