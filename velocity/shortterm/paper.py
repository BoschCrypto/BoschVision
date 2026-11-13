"""Paper trading of the frozen strategy, using the backtest's own logic.

    velocity paper                 follow live Coinbase prices, simulated fills
    velocity paper --once          one cycle, then exit

What is shared with the backtest, so the two cannot drift apart:
    strategies.setups_for   what CHARTIST and QUANT produce
    sim.size_position       WARDEN's sizing and shared budget
    sim.TradeState          every exit rule, bar by bar
    config.Instrument       every cost

Who does what here:
    Trader        ARBITER: one deterministic decision per asset per bar, with
                  the rule that decided it written to the decision log
    OrderRouter   the ONLY thing that submits, cancels or tracks orders
    PaperBroker   pretend exchange: fills at the next one-minute open
    StateStore    everything needed to restart, written after every bar

Live orders are off. There is no live broker adapter in this repository;
`live_orders_enabled` exists so that turning it on is a deliberate edit and,
today, an error rather than an order.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import numpy as np

from . import features, strategies
from .config import Instrument, RiskBudget
from .sim import TradeState, size_position

RULES = ["KILL_SWITCH", "STALE_FEED", "EXIT_STOP", "EXIT_TARGET", "EXIT_TIME", "EXIT_RULE",
         "DAILY_LOSS", "ENTER", "SKIP", "WAIT"]          # precedence, highest first
STALE_AFTER_S, FLATTEN_AFTER_S = 180, 900


class StateStore:
    def __init__(self, path: str, equity: float) -> None:
        self.path = path
        self.d = {"equity": equity, "start_equity": equity, "day": None, "day_start": equity,
                  "halted": False, "positions": {}, "orders": {}, "seen": [], "last_bar_ts": 0,
                  "trades": [], "next_order": 1}
        if os.path.exists(path):
            with open(path) as fh:
                self.d.update(json.load(fh))

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.d, fh)
        os.replace(tmp, self.path)       # atomic: a crash never leaves half a file


class PaperBroker:
    """Fills a market order at the next bar's open. `fill_ratio` below 1
    leaves a remainder working, to exercise the router's partial-fill path."""

    def __init__(self, fill_ratio: float = 1.0) -> None:
        self.fill_ratio = fill_ratio
        self.working: dict = {}

    def submit(self, order: dict) -> None:
        self.working[order["id"]] = order

    def cancel(self, order_id: str) -> None:
        self.working.pop(order_id, None)

    def on_bar(self, asset: str, bar: dict) -> list[dict]:
        fills = []
        for oid, o in list(self.working.items()):
            if o["asset"] != asset or bar["ts"] <= o["submitted_bar_ts"]:
                continue
            want = o["units"] - o["filled"]
            got = want if self.fill_ratio >= 1 else want * self.fill_ratio
            fills.append({"id": oid, "units": got, "price": bar["open"], "bar_ts": bar["ts"]})
            if got >= want - 1e-12:
                del self.working[oid]
        return fills


class LiveBrokerDisabled:
    def submit(self, order: dict) -> None:
        raise RuntimeError("Live order submission is disabled. This build has no live broker adapter.")

    cancel = on_bar = submit


class OrderRouter:
    """Single owner of order submission. Every order has a key; a key is
    accepted once, ever, so a repeated signal or a replay after a restart
    cannot double an order."""

    def __init__(self, store: StateStore, broker, timeout_s: int = 180) -> None:
        self.store, self.broker, self.timeout_s = store, broker, timeout_s
        for o in store.d["orders"].values():                 # restart: put working orders back
            if o["status"] in ("WORKING", "PARTIAL"):
                broker.submit(o)

    def submit(self, key: str, asset: str, side: int, units: float, purpose: str, bar_ts: int,
               meta: Optional[dict] = None) -> Optional[dict]:
        d = self.store.d
        if key in d["seen"]:
            return None                                       # duplicate signal
        d["seen"].append(key)
        del d["seen"][:-2000]
        o = {"id": f"o{d['next_order']}", "key": key, "asset": asset, "side": side, "units": units,
             "filled": 0.0, "avg_price": 0.0, "purpose": purpose, "status": "WORKING",
             "submitted_bar_ts": bar_ts, "meta": meta or {}}
        d["next_order"] += 1
        d["orders"][o["id"]] = o
        self.broker.submit(o)
        return o

    def on_bar(self, asset: str, bar: dict) -> list[dict]:
        """Apply fills and time out stale orders. Returns orders that changed."""
        changed, d = [], self.store.d
        for f in self.broker.on_bar(asset, bar):
            o = d["orders"].get(f["id"])
            if o is None or o["status"] not in ("WORKING", "PARTIAL"):
                continue
            tot = o["filled"] + f["units"]
            o["avg_price"] = (o["avg_price"] * o["filled"] + f["price"] * f["units"]) / tot
            o["filled"] = tot
            o["status"] = "FILLED" if tot >= o["units"] - 1e-12 else "PARTIAL"
            o["last_fill_bar_ts"] = f["bar_ts"]
            changed.append(o)
        for o in d["orders"].values():
            if (o["asset"] == asset and o["status"] in ("WORKING", "PARTIAL")
                    and bar["ts"] - o["submitted_bar_ts"] > self.timeout_s):
                self.broker.cancel(o["id"])
                o["status"] = "CANCELLED_PARTIAL" if o["filled"] > 0 else "TIMED_OUT"
                changed.append(o)
        for oid in [k for k, o in d["orders"].items() if o["status"] not in ("WORKING", "PARTIAL")][:-200]:
            del d["orders"][oid]
        return changed


class Trader:
    def __init__(self, cfg: dict, instruments: dict, budget: RiskBudget, store: StateStore,
                 router: OrderRouter, window: int = 14_400, log_path: Optional[str] = None,
                 kill_path: str = ".velocity_kill", sentinel=None) -> None:
        self.cfg, self.inst, self.budget = cfg, instruments, budget
        self.store, self.router, self.window = store, router, window
        self.log_path, self.kill_path, self.sentinel = log_path, kill_path, sentinel
        self.bars: dict = {a: [] for a in instruments}       # recent one-minute bars per asset
        self.state: dict = {}                                 # asset -> TradeState (rebuilt on restart)
        for a, p in store.d["positions"].items():
            self.state[a] = self._restore(a, p)

    # ---- helpers ---------------------------------------------------------

    def _restore(self, a: str, p: dict) -> TradeState:
        st = TradeState(self.inst[a], p["side"], p["entry"], p["stop"], None, p["trail"], p["time_min"], None)
        st.target, st.best, st.bars = p["target"], p["best"], p["bars"]
        return st

    def _log(self, rec: dict) -> None:
        if self.log_path:
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")

    def _m1(self, a: str) -> dict:
        b = self.bars[a][-self.window:]
        return {"ts": np.array([x["ts"] for x in b], dtype=np.int64),
                "open": np.array([x["open"] for x in b]), "high": np.array([x["high"] for x in b]),
                "low": np.array([x["low"] for x in b]), "close": np.array([x["close"] for x in b]),
                "volume": np.array([x["volume"] for x in b]), "gap": np.zeros(len(b), dtype=bool)}

    def sentinel_status(self) -> str:
        """MISSING is not the same as quiet. Neither changes trading: news was
        not backtested (no reliable archive of arrival times), so it is
        recorded, not acted on."""
        s = self.sentinel
        if s is None or getattr(s, "read_count", 0) == 0:
            return "MISSING"
        return "ACTIVE_EVENT" if s.latest[0] and time.time() - s.latest[2] < s.half_life_s else "ACTIVE_QUIET"

    # ---- one completed one-minute bar for one asset ----------------------

    def on_bar(self, a: str, bar: dict, now_s: Optional[float] = None) -> list[dict]:
        d, inst, out = self.store.d, self.inst[a], []
        if self.bars[a] and bar["ts"] <= self.bars[a][-1]["ts"]:
            return out                                        # already processed (restart overlap)
        self.bars[a].append(bar)
        del self.bars[a][:-self.window - 10]
        day = bar["ts"] // 86_400
        if d["day"] != day:
            d["day"], d["day_start"], d["halted"] = day, d["equity"], False

        # 1. fills from the router (entries placed last bar fill at this open)
        for o in self.router.on_bar(a, bar):
            if o["purpose"] == "entry" and o["filled"] > 0 and o["status"] in ("FILLED", "CANCELLED_PARTIAL"):
                m = o["meta"]
                px = o["avg_price"]
                if m["stop"] is not None and (px - m["stop"]) * o["side"] <= 0:
                    # filled beyond its own stop: out at once, costs paid both ways
                    self._close(a, bar, px, "stop (gapped)", inst.stop_cost(), o["filled"], opened=(px, o["side"]))
                    continue
                st = TradeState(inst, o["side"], px, m["stop"], m["r_mult"], m["trail"], m["time_min"], None)
                self.state[a] = st
                d["positions"][a] = {"side": o["side"], "entry": px, "units": o["filled"], "notional": o["filled"] * px,
                                     "stop": st.stop, "target": st.target, "trail": m["trail"], "time_min": m["time_min"],
                                     "best": st.best, "bars": 0, "entry_ts": bar["ts"], "key": m["key"],
                                     "rule_exit": bool(m.get("rule_exit")),
                                     "cost_in": inst.market_cost(), "stop0": m["stop"]}
                out.append({"event": "opened", "asset": a, "side": o["side"], "price": px, "units": o["filled"]})
            elif o["purpose"] == "exit" and o["filled"] > 0 and a in d["positions"]:
                self._close(a, bar, o["avg_price"], o["meta"]["how"], inst.market_cost(), o["filled"])
                out.append({"event": "closed", "asset": a, "how": o["meta"]["how"]})

        # 2. exits for an open position, same rules and precedence as the backtest
        stale = now_s is not None and now_s - (bar["ts"] + 60) > STALE_AFTER_S
        if a in d["positions"] and not any(o["asset"] == a and o["purpose"] == "exit" and o["status"] in ("WORKING", "PARTIAL")
                                           for o in d["orders"].values()):
            p, st = d["positions"][a], self.state[a]
            kill = os.path.exists(self.kill_path)
            if kill or (stale and now_s - (bar["ts"] + 60) > FLATTEN_AFTER_S):
                self._exit_order(a, bar, "kill switch" if kill else "stale feed")
            else:
                ev = st.on_bar(0, bar["open"], bar["high"], bar["low"], (bar["ts"] + 60) % 300 == 0,
                               self._atr(a))
                p.update({"stop": st.stop, "best": st.best, "bars": st.bars})
                if ev is not None:
                    price, how, cost, next_open = ev
                    if next_open:
                        self._exit_order(a, bar, how)
                    else:                                      # a resting stop or target: filled in this bar
                        self._close(a, bar, price, how, cost, p["units"])
                        out.append({"event": "closed", "asset": a, "how": how})

        # 3. decisions on 5-minute closes
        if (bar["ts"] + 60) % 300 == 0 and len(self.bars[a]) >= min(self.window, 4_400):
            out.append(self._decide(a, bar, stale))
        d["last_bar_ts"] = max(d["last_bar_ts"], bar["ts"])
        self.store.save()
        return out

    def _atr(self, a: str) -> float:
        b = self.bars[a][-125:]
        if len(b) < 125:
            return 0.0
        tr, n = [], len(b) // 5
        for k in range(n - 24, n):
            ch = b[len(b) - (n - k) * 5: len(b) - (n - k - 1) * 5]
            pc = b[len(b) - (n - k) * 5 - 1]["close"]
            hi, lo = max(x["high"] for x in ch), min(x["low"] for x in ch)
            tr.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        return sum(tr) / len(tr)

    def _exit_order(self, a: str, bar: dict, how: str) -> None:
        p = self.store.d["positions"][a]
        self.router.submit(f"exit:{a}:{p['key']}:{how}", a, -p["side"], p["units"], "exit", bar["ts"], {"how": how})

    def _close(self, a: str, bar: dict, price: float, how: str, cost_out: float, units: float, opened=None) -> None:
        d, inst = self.store.d, self.inst[a]
        if opened is not None:
            entry, side, cost_in, entry_ts, key = opened[0], opened[1], inst.market_cost(), bar["ts"], "-"
        else:
            p = d["positions"].pop(a)
            self.state.pop(a, None)
            entry, side, cost_in, entry_ts, key = p["entry"], p["side"], p["cost_in"], p["entry_ts"], p["key"]
        notional = units * entry
        net = side * (price / entry - 1) - cost_in - cost_out
        d["equity"] += notional * net
        d["trades"].append({"asset": a, "side": side, "entry_ts": entry_ts, "exit_ts": bar["ts"], "entry": entry,
                            "exit": price, "units": units, "how": how, "net_bps": round(net * 1e4, 2),
                            "pnl": round(notional * net, 4), "equity_after": round(d["equity"], 4), "key": key})
        del d["trades"][:-5000]
        if d["equity"] - d["day_start"] <= -self.budget.daily_loss * d["day_start"] and not d["halted"]:
            d["halted"] = True
            for b in list(d["positions"]):
                self._exit_order(b, bar, "daily loss limit")
        self._log({"ts": bar["ts"] + 60, "asset": a, "rule": {"stop": "EXIT_STOP", "stop (gapped)": "EXIT_STOP",
                   "target": "EXIT_TARGET", "time limit": "EXIT_TIME", "rule exit": "EXIT_RULE",
                   "kill switch": "KILL_SWITCH", "stale feed": "STALE_FEED",
                   "daily loss limit": "DAILY_LOSS"}.get(how, "EXIT_RULE"),
                   "action": "closed", "how": how, "price": price, "net_bps": round(net * 1e4, 2),
                   "equity": round(d["equity"], 2)})

    def _decide(self, a: str, bar: dict, stale: bool) -> dict:
        """ARBITER. Returns and logs one decision with the rule that made it."""
        d, inst = self.store.d, self.inst[a]
        m1 = self._m1(a)
        f = features.build(m1)
        setups = strategies.setups_for(m1, f, self.cfg, a)
        n = len(m1["ts"])
        T = self.cfg.get("T", 24)
        k = int(f["k_of_min"][n - 1])
        z = float(np.nan_to_num(f.get(f"hz{T}", f["hz24"])[k]))
        know = {"quant": {"hourly_z": round(z, 3), "trend": int(np.sign(z)), "lookback_hours": T},
                "chartist": {"setup": None}, "sentinel": self.sentinel_status(),
                "warden": {"equity": round(d["equity"], 2), "halted_today": d["halted"]}}
        rec = {"ts": bar["ts"] + 60, "asset": a, "price": bar["close"], "knew": know}
        pos = d["positions"].get(a)
        flip_pnl = 0.0

        def done(rule: str, action: str, why: str) -> dict:
            rec.update({"rule": rule, "action": action, "why": why})
            self._log(rec)
            return rec

        if os.path.exists(self.kill_path):
            return done("KILL_SWITCH", "wait", "kill switch file is present")
        if stale:
            return done("STALE_FEED", "wait", "latest bar is more than 3 minutes old: no new entries")
        exit_working = any(o["asset"] == a and o["purpose"] == "exit" and o["status"] in ("WORKING", "PARTIAL")
                           for o in d["orders"].values())
        if pos is not None and exit_working:
            # An exit was already decided on this bar (a time limit). Like a
            # flip, a new setup on the same bar may be taken; size it on the
            # balance as it will be after that exit.
            flip_pnl = pos["notional"] * (pos["side"] * (bar["close"] / pos["entry"] - 1)
                                          - pos["cost_in"] - inst.market_cost())
        elif pos is not None:
            # Does the rule still want this side? Asked of the rule's CURRENT
            # state, not by finding the run that opened the trade: that run's
            # start scrolls out of the data window on a long trend, and a
            # lookup by start time would then never find it again.
            if not pos.get("rule_exit"):
                return done("WAIT", "hold", "position open; exits are its stop, target or time limit")
            still = any(s["side"] == pos["side"] and s["i"] <= n - 1 and s.get("rule_exit")
                        and (s.get("exit_i") is None or s["exit_i"] > n - 1) for s in setups)
            if still:
                return done("WAIT", "hold", "position open; the rule still holds")
            self._exit_order(a, bar, "rule exit")
            self._log(dict(rec, rule="EXIT_RULE", action="exit",
                           why="the rule that opened this position no longer holds"))
            # Size any new side on the balance as it will be once this exit
            # fills (estimated at the current close), as the backtest does.
            flip_pnl = pos["notional"] * (pos["side"] * (bar["close"] / pos["entry"] - 1)
                                          - pos["cost_in"] - inst.market_cost())
            # fall through: a rule that flips sides opens the new side on this same bar
        now_setups = [s for s in setups if s["i"] == n - 1]
        if not now_setups:
            if pos is not None:
                rec.update({"rule": "EXIT_RULE", "action": "exit", "why": "the rule that opened this position no longer holds"})
                return rec
            return done("WAIT", "wait", "no setup on this bar")
        s = now_setups[0]
        know["chartist"]["setup"] = {"side": s["side"], "entry": s["entry"], "stop": s["stop"], "reject": s["reject"]}
        if s["reject"]:
            return done("SKIP", "wait", s["reject"])
        if s["entry"] != "market":
            return done("SKIP", "wait", "resting stop-entry orders are not supported in paper mode yet")
        if not inst.can_short and s["side"] < 0:
            return done("SKIP", "wait", "this instrument cannot be sold short")
        if d["halted"]:
            return done("DAILY_LOSS", "wait", "daily loss limit reached; no entries until the next UTC day")
        px = bar["close"]
        stop_dist = abs(px - s["stop"]) / px if s["stop"] is not None else None
        other_n = sum(p["notional"] for b, p in d["positions"].items() if b != a)
        other_r = sum(p["notional"] * (abs(p["entry"] - p["stop0"]) / p["entry"] if p["stop0"] else self.budget.nominal_stop)
                      for b, p in d["positions"].items() if b != a)
        # An entry order sent a moment ago for the other asset is exposure
        # too, even though it has not filled. Without this, two signals in
        # the same minute were each sized as if alone and together broke the
        # shared limit.
        for o in d["orders"].values():
            if o["purpose"] == "entry" and o["status"] in ("WORKING", "PARTIAL") and o["asset"] != a:
                other_n += o["meta"].get("notional", 0.0)
                other_r += o["meta"].get("risk", 0.0)
        units, notional, binding = size_position(self.budget, inst, d["equity"] + flip_pnl, px, stop_dist,
                                                 other_n, other_r)
        know["warden"].update({"units": units, "notional": round(notional, 2), "sized_by": binding})
        if units <= 0:
            return done("SKIP", "wait", "WARDEN: " + binding)
        key = f"{a}:{self.cfg['id']}:{int(m1['ts'][s['i']])}:{s['side']}"
        o = self.router.submit("entry:" + key, a, s["side"], units, "entry", bar["ts"],
                               {"stop": s["stop"], "r_mult": s["r_mult"], "trail": s["trail"],
                                "time_min": s["time_min"], "key": key, "rule_exit": bool(s.get("rule_exit")),
                                "notional": notional, "risk": notional * (stop_dist or self.budget.nominal_stop)})
        if o is None:
            return done("SKIP", "wait", "this signal was already acted on")
        return done("ENTER", "long" if s["side"] > 0 else "short", f"setup known at this bar; {units:g} units")


def run_live(cfg_path: str, state_path: str, log_path: str, once: bool = False, poll_s: float = 5.0) -> None:
    from .data import recent
    with open(cfg_path) as fh:
        frozen = json.load(fh)
    if frozen.get("live_orders_enabled"):
        raise SystemExit("live_orders_enabled is set, but this build has no live adapter. Refusing to start.")
    inst = {a: Instrument(**v) for a, v in frozen["instruments"].items()}
    budget = RiskBudget(**frozen["risk"])
    store = StateStore(state_path, budget.equity)
    trader = Trader(frozen["config"], inst, budget, store, OrderRouter(store, PaperBroker()), log_path=log_path)
    print(f"PAPER trading {frozen['config']['id']} on {', '.join(inst)}. No real orders are sent.")
    if not frozen.get("qualified_in_research", False):
        print("NOTE: this strategy did not qualify in research and lost on its final test. "
              "Running it is an observation exercise, not a recommendation.")
    for a in inst:                                           # warm up, without acting on history
        hist = recent(f"{a}-USD", trader.window + 5)
        cut = store.d["last_bar_ts"] or hist[-1]["ts"]
        trader.bars[a] = [b for b in hist if b["ts"] <= cut][-trader.window:]
    print(f"warmed up on {min(len(v) for v in trader.bars.values()):,} one-minute bars; equity ${store.d['equity']:,.2f}")
    while True:
        now = time.time()
        for a in inst:
            try:
                for b in recent(f"{a}-USD", 12):
                    if b["ts"] + 60 <= now - 3 and b["ts"] > trader.bars[a][-1]["ts"]:
                        for ev in trader.on_bar(a, b, now_s=now):
                            if ev.get("rule") not in (None, "WAIT") or ev.get("event"):
                                print(time.strftime("%H:%M", time.gmtime(b["ts"] + 60)), a, ev.get("rule") or ev.get("event"),
                                      ev.get("action", ""), ev.get("why", ev.get("how", "")), flush=True)
            except Exception as e:                            # network trouble must not end the session
                print(f"feed trouble for {a}: {type(e).__name__}; retrying", flush=True)
        if once:
            return
        time.sleep(poll_s)
