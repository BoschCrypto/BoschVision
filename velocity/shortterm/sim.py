"""Fill simulation and the shared-balance account.

Two layers:

  resolve()    what happens to ONE setup on the one-minute tape: is the
               entry filled, and how and when does the trade end. Depends on
               prices and the instrument's costs only, never on the account.
               Paper trading uses the same per-bar rules through TradeState.

  portfolio()  the account: setups from both assets in the order they became
               known, WARDEN's sizing against one balance and a shared risk
               budget, the daily loss limit, and a log of everything skipped.

Rules that keep a false edge out (PLAN.md section 3):
  - a market order sent at a 5-minute close fills at the NEXT one-minute open
  - a limit (profit target) fills only if price trades through it
  - stop and target in the same one-minute bar: the stop came first
  - a stop gapped through fills at the open, not at the stop price
  - a stop-entry order filled inside a bar that also reaches its stop is a loss
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import Instrument, RiskBudget


class Market:
    """One asset's one-minute tape plus what the fill rules need."""

    def __init__(self, asset: str, m1: dict, feats: dict, inst: Instrument,
                 funding_cum: Optional[np.ndarray] = None) -> None:
        self.asset, self.inst = asset, inst
        self.ts = m1["ts"]
        self.o, self.h, self.l, self.c = (m1[k].tolist() for k in ("open", "high", "low", "close"))
        self.n = len(self.o)
        self.is5 = feats["is5end"].tolist()
        k = np.maximum(feats["k_of_min"], 0)
        self.atr1 = np.nan_to_num(feats["atr"][k]).tolist()      # ATR of the last completed 5m bar
        self.fund = funding_cum                                    # cumulative rate a long has paid
        self._memo: dict = {}


class TradeState:
    """An open trade's exit rules, one bar at a time. The backtest and the
    paper trader both drive this, so they cannot disagree about an exit."""

    __slots__ = ("side", "entry", "stop", "target", "trail", "time_min", "exit_i", "best",
                 "bars", "inst", "stop_entry", "slip_mult")

    def __init__(self, inst: Instrument, side: int, entry: float, stop: Optional[float],
                 r_mult: Optional[float], trail: Optional[float], time_min: Optional[int],
                 exit_i: Optional[int], stop_entry: bool = False, slip_mult: float = 1.0) -> None:
        self.inst, self.side, self.entry, self.stop = inst, side, entry, stop
        self.target = (entry + side * r_mult * abs(entry - stop)) if (r_mult and stop is not None) else None
        self.trail, self.time_min, self.exit_i = trail, time_min, exit_i
        self.best, self.bars, self.stop_entry, self.slip_mult = entry, 0, stop_entry, slip_mult

    def on_bar(self, i: int, o: float, h: float, l: float, is5: bool, atr: float):
        """Returns None, or (price, how, cost_fraction, fills_next_open)."""
        first = self.bars == 0
        self.bars += 1
        s, inst = self.side, self.inst
        if self.stop is not None:
            if s > 0:
                if not first and o <= self.stop:
                    return o, "stop (gapped)", inst.stop_cost(self.slip_mult), False
                if l <= self.stop:
                    return self.stop, "stop", inst.stop_cost(self.slip_mult), False
            else:
                if not first and o >= self.stop:
                    return o, "stop (gapped)", inst.stop_cost(self.slip_mult), False
                if h >= self.stop:
                    return self.stop, "stop", inst.stop_cost(self.slip_mult), False
        if self.target is not None and not (first and self.stop_entry):
            thr = inst.through
            if (s > 0 and h >= self.target * (1 + thr)) or (s < 0 and l <= self.target * (1 - thr)):
                return self.target, "target", inst.limit_cost(), False
        if self.time_min is not None and self.bars >= self.time_min:
            return None, "time limit", inst.market_cost(self.slip_mult), True
        if self.exit_i is not None and i >= self.exit_i:
            return None, "rule exit", inst.market_cost(self.slip_mult), True
        if s > 0:
            self.best = max(self.best, h)
        else:
            self.best = min(self.best, l)
        if self.trail is not None and is5 and atr > 0:
            t = self.best - s * self.trail * atr
            self.stop = max(self.stop, t) if s > 0 else min(self.stop, t)
        return None


def resolve(mk: Market, s: dict, end_i: Optional[int] = None, delay_bars: int = 0,
            slip_mult: float = 1.0) -> dict:
    """What happens to setup `s`. `end_i` is the first one-minute index the
    trade may NOT use (a fold boundary): anything still open is closed there."""
    end_i = mk.n - 1 if end_i is None else min(end_i, mk.n - 1)
    key = (s["uid"], delay_bars, slip_mult)
    hit = mk._memo.get(key)
    if hit is not None and (not hit["filled"] or hit["exit_i"] < end_i) and hit.get("_end", mk.n) >= end_i:
        return hit
    out = _resolve(mk, s, end_i, delay_bars, slip_mult)
    out["_end"] = end_i
    if key not in mk._memo or end_i >= mk._memo[key].get("_end", -1):
        mk._memo[key] = out
    return out


def _resolve(mk: Market, s: dict, end_i: int, delay_bars: int, slip_mult: float) -> dict:
    inst, o, h, l = mk.inst, mk.o, mk.h, mk.l
    j0 = s["i"] + 1 + 5 * delay_bars
    if s.get("reject"):
        return {"filled": False, "why": s["reject"]}
    if j0 >= end_i:
        return {"filled": False, "why": "too close to the end of the window"}
    stop_entry = s["entry"] == "stop"
    if not stop_entry:
        side, stop, j, px, cost_in = s["side"], s["stop"], j0, o[j0], inst.market_cost(slip_mult)
        if stop is not None and (px - stop) * side <= 0:
            # A market order fills wherever the market is. If that is already
            # beyond the stop, the trade is opened and stopped at once and
            # pays both costs. (This used to be treated as "not filled",
            # which quietly avoided a loss a real order would have taken.)
            c = cost_in + inst.stop_cost(slip_mult)
            return {"filled": True, "side": side, "entry_i": j, "entry": px, "exit_i": j, "exit": px,
                    "how": "stop (gapped)", "gross": 0.0, "cost": c, "funding": 0.0, "net": -c,
                    "stop_dist": None, "hold_min": 0, "mfe": 0.0, "mae": 0.0, "stop0": stop}
    else:
        side = 0
        for j in range(j0, min(s["expiry_i"], end_i - 1) + 1):
            best = None
            for sd, trig, stp in s["legs"]:
                if (sd > 0 and h[j] >= trig) or (sd < 0 and l[j] <= trig):
                    fill = max(trig, o[j]) if sd > 0 else min(trig, o[j])
                    d = abs(o[j] - trig)
                    if best is None or d < best[0]:
                        best = (d, sd, fill, stp)
            if best is not None:
                _, side, px, stop = best
                break
        if side == 0:
            return {"filled": False, "why": "entry price never reached before the order expired"}
        cost_in = inst.stop_cost(slip_mult)
    if not inst.can_short and side < 0:
        return {"filled": False, "why": "this instrument cannot be sold short"}

    st = TradeState(inst, side, px, stop, s["r_mult"], s["trail"], s["time_min"], s.get("exit_i"),
                    stop_entry=stop_entry, slip_mult=slip_mult)
    entry_i, mfe, mae = j, 0.0, 0.0
    while True:
        if j >= end_i:
            xp, how, cost_out, xi = o[end_i], "window end", inst.market_cost(slip_mult), end_i
            break
        ev = st.on_bar(j, o[j], h[j], l[j], mk.is5[j], mk.atr1[j])
        mfe = max(mfe, (h[j] / px - 1) * side if side > 0 else (1 - l[j] / px))
        mae = min(mae, (l[j] / px - 1) if side > 0 else (1 - h[j] / px))
        if ev is not None:
            xp, how, cost_out, nxt = ev
            xi = j
            if nxt:
                xi = min(j + 1, mk.n - 1)
                xp = o[xi]
            break
        j += 1
    gross = side * (xp / px - 1.0)
    funding = 0.0
    if inst.pays_funding and mk.fund is not None:
        funding = side * float(mk.fund[xi] - mk.fund[entry_i])
    risk = abs(px - stop) / px if stop is not None else None
    return {"filled": True, "side": side, "entry_i": entry_i, "entry": px, "exit_i": xi, "exit": xp,
            "how": how, "gross": gross, "cost": cost_in + cost_out, "funding": funding,
            "net": gross - cost_in - cost_out - funding, "stop_dist": risk,
            "hold_min": xi - entry_i, "mfe": mfe, "mae": mae, "stop0": stop}


# ------------------------------------------------------------------ WARDEN

def size_position(budget: RiskBudget, inst: Instrument, equity: float, price: float,
                  stop_dist: Optional[float], other_notional: float = 0.0,
                  other_risk: float = 0.0) -> tuple[float, float, str]:
    """Units and notional WARDEN allows, or (0, 0, reason). Risk a fixed share
    of equity to the stop, capped by the position limit and by what the other
    asset's open position has already used of the shared budget."""
    d = stop_dist if stop_dist else budget.nominal_stop
    want = budget.risk_per_trade * equity / d
    caps = [(budget.max_position * equity, "position limit"),
            (budget.combined_position * equity - other_notional, "shared position limit"),
            ((budget.combined_risk * equity - other_risk) / d, "shared risk limit")]
    notional, binding = want, "risk per trade"
    for cap, name in caps:
        if cap < notional:
            notional, binding = cap, name
    if notional <= 0:
        return 0.0, 0.0, f"no room left under the {binding}"
    if inst.contract > 0:
        unit = inst.contract * price
        n = int(notional // unit)
        if n < 1:
            return 0.0, 0.0, f"one contract (${unit:,.0f}) is more than the {binding} allows (${notional:,.0f})"
        return n * inst.contract, n * unit, binding
    return notional / price, notional, binding


# --------------------------------------------------------------- account

@dataclass
class Result:
    trades: list
    skipped: list
    equity: list            # (ts, equity) after each closed trade
    start_equity: float
    i0_ts: int
    i1_ts: int


def portfolio(markets: dict, setups: dict, budget: RiskBudget, i0: int, i1: int,
              delay_bars: int = 0, slip_mult: float = 1.0, log_skips: bool = True,
              start_equity: Optional[float] = None) -> Result:
    """Run the account over one-minute indices [i0, i1). `setups` maps asset
    to its list. All markets share one index (same grid)."""
    any_mk = next(iter(markets.values()))
    ts = any_mk.ts
    eq = start_equity if start_equity is not None else budget.equity
    merged = sorted((s for a in setups for s in setups[a] if i0 <= s["i"] < i1),
                    key=lambda s: (s["i"], s["asset"]))
    open_pos: dict = {}                  # asset -> position dict
    busy_until = {a: -1 for a in markets}
    trades, skipped, curve = [], [], [(int(ts[i0]), eq)]
    day, day_start, halted = int(ts[i0]) // 86_400, eq, False

    def close_due(now_i: int) -> None:
        nonlocal eq, day, day_start, halted
        while open_pos:
            a = min(open_pos, key=lambda k: open_pos[k]["exit_i"])
            p = open_pos[a]
            if p["exit_i"] > now_i:
                break
            del open_pos[a]
            d = int(ts[p["exit_i"]]) // 86_400
            if d != day:
                day, day_start, halted = d, eq, False
            eq += p["pnl"]
            p["equity_after"] = eq
            trades.append(p)
            curve.append((int(ts[p["exit_i"]]), eq))
            if eq - day_start <= -budget.daily_loss * day_start and not halted:
                halted = True
                for b in list(open_pos):                     # flatten the other position now
                    q = open_pos[b]
                    if q["exit_i"] > p["exit_i"] + 1:
                        r2 = resolve(markets[b], q["setup"], end_i=p["exit_i"] + 1,
                                     delay_bars=delay_bars, slip_mult=slip_mult)
                        if r2["filled"]:
                            q.update(_fill_fields(r2, q["units"], q["notional"], ts))
                            q["how"] = "daily loss limit"

    for s in merged:
        close_due(s["i"])
        # A rule that flips sides announces the exit and the new entry at the
        # same moment; both fill at the next open. The exit is settled first,
        # otherwise the new side is refused as "already in a position".
        # Only for exits already DECIDED at this bar (a rule or time-limit
        # exit, which fills at the next open). A stop or target that will be
        # hit during the next minute is not known yet; treating it as closed
        # would be reading the future.
        p_same = open_pos.get(s["asset"])
        if (p_same is not None and p_same["how"] in ("rule exit", "time limit")
                and p_same["exit_i"] <= s["i"] + 1 + 5 * delay_bars):
            close_due(p_same["exit_i"])
        d = int(ts[s["i"]]) // 86_400
        if d != day:
            day, day_start, halted = d, eq, False
        a = s["asset"]
        mk = markets[a]
        why = ""
        if s.get("reject"):
            why = s["reject"]
        elif halted:
            why = "daily loss limit reached"
        elif a in open_pos:
            why = "already in a position"
        elif s["i"] <= busy_until[a]:
            why = "an order for this asset is still working"
        r = None
        if not why:
            r = resolve(mk, s, end_i=i1, delay_bars=delay_bars, slip_mult=slip_mult)
            if s["entry"] == "stop":
                busy_until[a] = s["expiry_i"] if not r["filled"] else r["entry_i"]
            if not r["filled"]:
                why = r["why"]
        if not why:
            other_n = sum(p["notional"] for p in open_pos.values())
            other_r = sum(p["risk"] for p in open_pos.values())
            units, notional, binding = size_position(budget, mk.inst, eq, r["entry"], r["stop_dist"],
                                                     other_n, other_r)
            if units <= 0:
                why = "WARDEN: " + binding
        if why:
            if log_skips:
                hyp = None
                if not s.get("reject"):
                    hyp = r if (r and r["filled"]) else resolve(mk, s, end_i=i1, delay_bars=delay_bars,
                                                                slip_mult=slip_mult)
                skipped.append({"ts": int(ts[s["i"]]), "asset": a, "cfg": s["cfg"], "side": s["side"],
                                "why": why, "q": s.get("q"),
                                "hyp_net_bps": round(hyp["net"] * 1e4, 2) if hyp and hyp["filled"] else None})
            continue
        pos = {"asset": a, "cfg": s["cfg"], "setup": s, "units": units, "notional": notional,
               "risk": notional * (r["stop_dist"] or budget.nominal_stop), "sized_by": binding,
               "known_ts": int(ts[s["i"]]), "q": s.get("q")}
        pos.update(_fill_fields(r, units, notional, ts))
        open_pos[a] = pos
    close_due(i1 + 10**9)
    return Result(trades, skipped, curve, curve[0][1], int(ts[i0]), int(ts[min(i1, len(ts) - 1)]))


def _fill_fields(r: dict, units: float, notional: float, ts) -> dict:
    return {"side": r["side"], "entry_i": r["entry_i"], "exit_i": r["exit_i"],
            "entry_ts": int(ts[r["entry_i"]]), "exit_ts": int(ts[r["exit_i"]]),
            "entry": r["entry"], "exit": r["exit"], "how": r["how"], "stop0": r["stop0"],
            "gross_bps": r["gross"] * 1e4, "cost_bps": r["cost"] * 1e4, "funding_bps": r["funding"] * 1e4,
            "net_bps": r["net"] * 1e4, "pnl": notional * r["net"], "cost_usd": notional * r["cost"],
            "funding_usd": notional * r["funding"], "hold_min": r["hold_min"]}


def stats(res: Result, assets=("BTC", "ETH")) -> dict:
    out = {}
    span_min = max(1, (res.i1_ts - res.i0_ts) / 60)
    years = span_min / 525_600
    for name in list(assets) + ["ALL"]:
        tr = [t for t in res.trades if name == "ALL" or t["asset"] == name]
        pnl = [t["pnl"] for t in tr]
        wins = sum(x for x in pnl if x > 0)
        losses = -sum(x for x in pnl if x < 0)
        out[name] = {
            "trades": len(tr), "net_usd": sum(pnl), "net_pct": sum(pnl) / res.start_equity * 100,
            "avg_net_bps": float(np.mean([t["net_bps"] for t in tr])) if tr else 0.0,
            "win_rate": sum(1 for x in pnl if x > 0) / len(tr) if tr else 0.0,
            "profit_factor": (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0),
            "costs_usd": sum(t["cost_usd"] + t["funding_usd"] for t in tr),
            "time_in_market": sum(t["hold_min"] for t in tr) / span_min,
        }
    eqs = [e for _, e in res.equity]
    peak, mdd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    total = eqs[-1] / eqs[0] - 1
    out["ALL"].update({"total_return": total, "max_drawdown": mdd, "years": years,
                       "cagr": (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0,
                       "end_equity": eqs[-1]})
    return out
