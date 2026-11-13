"""Record what every agent said during a run, for the dashboard.

Watches a run through Engine.run's observer hook and writes five small JSON
files, each `{rows, meta}`:

  timeline   price, each agent's vote, the arbiter's score and the position,
             averaged into time buckets
  trades     one row per closed trade, with the votes that opened it
  gates      what happened to every bar on which the book was flat
  pairs      how often QUANT and CHARTIST agreed, and whether the market
             then moved their way
  links      counts of each agent-to-agent interaction

Nothing here feeds back into trading.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from .engine import Engine
from .types import NS_PER_S, Decision, Tick

_NUM = re.compile(r"[-+]?[\d,]*\.?\d+x?")
_DISP = re.compile(r"disp=([\d.]+) size x([\d.]+)")
VOTE_FLOOR = 0.05          # same cut the arbiter uses to count a vote


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / NS_PER_S, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Recorder:
    def __init__(self, engine: Engine, bucket_s: int = 900,
                 forward_bars: Optional[int] = None) -> None:
        self.eng = engine
        self.bucket_ns = bucket_s * NS_PER_S
        self.forward = forward_bars or engine.warden.limits.horizon_ticks
        self.px: list[float] = []
        self.q: list[float] = []
        self.c: list[float] = []
        self.timeline: list[dict] = []
        self._b = None                     # open bucket accumulator
        self.gates: dict[str, int] = {}
        self.entries: list[dict] = []
        self.breaks = {"with": 0, "against": 0, "trap": 0}
        self.news_live = 0
        self.bars = 0

    # -- per tick ---------------------------------------------------------

    def observe(self, tick: Tick, d: Decision) -> None:
        eng, bb = self.eng, self.eng.bb
        now = tick.ts_ns
        q = bb.read("QUANT", now)
        c = bb.read("CHARTIST", now)
        score = eng.arbiter._last_score
        self.bars += 1
        self.px.append(tick.price)
        self.q.append(q)
        self.c.append(c)
        if bb.read("SENTINEL", now) != 0.0:
            self.news_live += 1

        # time bucket
        k = now // self.bucket_ns
        b = self._b
        if b is None or b["k"] != k:
            self._flush()
            b = self._b = {"k": k, "n": 0, "q": 0.0, "c": 0.0, "s": 0.0}
        b["n"] += 1
        b["q"] += q
        b["c"] += c
        b["s"] += score
        b["px"] = tick.price
        b["pos"] = bb.open_side
        b["ts"] = now

        # CHARTIST reading QUANT: what kind of break was this?
        ch = eng.chartist
        if ch._since_break == 0:
            sig = bb.get("CHARTIST")
            if sig is not None and "TRAP" in sig.reason:
                self.breaks["trap"] += 1
            elif q * ch._break_dir > 0.02:
                self.breaks["with"] += 1
            else:
                self.breaks["against"] += 1

        # what the arbiter decided
        r = d.reason
        if d.is_trade:
            key = "Trade opened"
            m = _DISP.search(r)
            self.entries.append({
                "quant": round(q, 3), "chartist": round(c, 3), "score": round(d.score, 3),
                "split": float(m.group(1)) if m else None,
                "size_x": float(m.group(2)) if m else None,
            })
        elif r.startswith("hold"):
            key = "In a position: holding"
        elif r.startswith("exit"):
            key = "In a position: exit signal"
        elif r.startswith("no edge"):
            key = "Score below the entry bar"
        elif r.startswith("no confluence"):
            key = "Score high enough, but only one agent behind it"
        elif r.startswith("SENTINEL veto"):
            key = "SENTINEL veto: news against the trade"
        elif r.startswith("WARDEN veto"):
            why = _NUM.sub("#", r.split(":", 1)[1]).strip()
            key = "WARDEN veto: " + _WARDEN_WORDS.get(why.split(" (")[0].split(" — ")[0], why)
        elif r.startswith("below minimum size"):
            key = "Too split to deserve the one contract available"
        else:
            key = r[:50] or "other"
        self.gates[key] = self.gates.get(key, 0) + 1

    def _flush(self) -> None:
        b = self._b
        if not b or not b["n"]:
            return
        n = b["n"]
        self.timeline.append({
            "t": _iso(b["ts"]), "px": round(b["px"], 2),
            "quant": round(b["q"] / n, 3), "chartist": round(b["c"] / n, 3),
            "score": round(b["s"] / n, 3), "pos": b["pos"],
        })

    # -- run + write ------------------------------------------------------

    def run(self, feed) -> dict:
        report = self.eng.run(feed, observer=self.observe)
        self._flush()
        self.report = report
        return report

    def _trades(self) -> list[dict]:
        rows = []
        for i, t in enumerate(self.eng.executor.trades):
            e = self.entries[i] if i < len(self.entries) else {}
            gross = (t.exit - t.entry) * t.qty * t.side
            out = t.reason_out
            exit_kind = ("Risk stop" if out.startswith("risk flatten")
                         else "Target" if out == "target" else "Stop" if out == "stop"
                         else "Signal flipped" if "flip" in out
                         else "Signal faded" if out.startswith("exit")
                         else "End of data" if "end" in out else out)
            rows.append({
                "n": i + 1, "opened": _iso(t.entry_ns), "closed": _iso(t.exit_ns),
                "side": "Long" if t.side > 0 else "Short",
                "entry": round(t.entry, 2), "exit": round(t.exit, 2),
                "held_min": round(t.held_s / 60),
                "move": round(gross, 2), "fees": round(t.costs, 2), "net": round(t.pnl, 2),
                "exit_by": exit_kind,
                "quant": e.get("quant"), "chartist": e.get("chartist"),
                "score": e.get("score"), "split": e.get("split"), "size_x": e.get("size_x"),
            })
        return rows

    def _pairs(self) -> list[dict]:
        px, q, c, h = self.px, self.q, self.c, self.forward
        acc = {k: [0, 0, 0.0] for k in ("long", "short", "opposed", "neutral", "base")}
        n = len(px) - h
        for i in range(max(0, n)):
            fwd = (px[i + h] / px[i] - 1.0) * 1e4
            a = acc["base"]; a[0] += 1; a[1] += fwd > 0; a[2] += fwd
            ql = q[i] > VOTE_FLOOR; qs = q[i] < -VOTE_FLOOR
            cl = c[i] > VOTE_FLOOR; cs = c[i] < -VOTE_FLOOR
            if ql and cl:
                a = acc["long"]; a[0] += 1; a[1] += fwd > 0; a[2] += fwd
            elif qs and cs:
                a = acc["short"]; a[0] += 1; a[1] += fwd < 0; a[2] -= fwd
            elif (ql and cs) or (qs and cl):
                a = acc["opposed"]; sgn = 1.0 if ql else -1.0
                a[0] += 1; a[1] += fwd * sgn > 0; a[2] += fwd * sgn
            else:
                acc["neutral"][0] += 1
        total = max(1, n)
        names = [("long", "Both say long"), ("short", "Both say short"),
                 ("opposed", "They disagree (scored QUANT's way)"),
                 ("neutral", "One or both have no view"),
                 ("base", "Every bar, as if always long")]
        rows = []
        for k, label in names:
            cnt, hit, mv = acc[k]
            rows.append({
                "state": label, "bars": cnt,
                "share_pct": round(cnt / total * 100, 1),
                "right_pct": round(hit / cnt * 100, 1) if cnt and k != "neutral" else None,
                "move_bps": round(mv / cnt, 1) if cnt and k != "neutral" else None,
            })
        return rows

    def _gates(self) -> list[dict]:
        order = sorted(self.gates.items(), key=lambda kv: -kv[1])
        flat = sum(v for k, v in order if not k.startswith("In a position"))
        rows = []
        for k, v in order:
            in_pos = k.startswith("In a position")
            rows.append({"outcome": k, "bars": v, "book": "In a position" if in_pos else "Flat",
                         "share_pct": None if in_pos else round(v / max(1, flat) * 100, 2)})
        return rows

    def _links(self) -> list[dict]:
        g = self.gates
        veto = sum(v for k, v in g.items() if k.startswith("WARDEN veto"))
        opened = g.get("Trade opened", 0)
        small = g.get("Too split to deserve the one contract available", 0)
        proposals = veto + opened + small
        b = self.breaks
        rows = [
            ("breaks_with", "Level breaks CHARTIST scored at full weight because QUANT's trend agreed", b["with"]),
            ("breaks_against", "Level breaks CHARTIST cut in half because they ran against QUANT's trend", b["against"]),
            ("traps", "Unconfirmed breaks CHARTIST faded as traps", b["trap"]),
            ("agreed", "Bars where both voted the same way and the score cleared the entry bar", proposals),
            ("one_agent", "Bars where the score was high enough but only one agent was behind it",
             g.get("Score high enough, but only one agent behind it", 0)),
            ("refused", "Proposed entries WARDEN refused", veto),
            ("approved", "Proposed entries WARDEN approved and sized", opened + small),
            ("too_split", "Approved entries skipped because the agents were too split", small),
            ("opened", "Trades opened", opened),
            ("news_bars", "Bars with a live news signal from SENTINEL", self.news_live),
        ]
        return [{"id": i, "what": w, "count": n} for i, w, n in rows]

    def write(self, out_dir: str, command: str = "", source_note: str = "") -> dict:
        os.makedirs(out_dir, exist_ok=True)
        meta = []
        if command:
            meta.append({"label": "Made by", "value": command, "type": "code", "lang": "bash"})
        if source_note:
            meta.append({"label": "Input", "value": source_note})
        meta.append({"label": "Recorder", "value": "velocity/recorder.py in the BoschVision repository"})
        tables = {"timeline": self.timeline, "trades": self._trades(), "gates": self._gates(),
                  "pairs": self._pairs(), "links": self._links()}
        sizes = {}
        for name, rows in tables.items():
            path = os.path.join(out_dir, f"{name}.json")
            with open(path, "w") as fh:
                json.dump({"rows": rows, "meta": meta}, fh, separators=(",", ":"))
            sizes[name] = (len(rows), os.path.getsize(path))
        return sizes


_WARDEN_WORDS = {
    "cooldown #s/#s": "cooling down after the last exit",
    "# losses in a row": "losing streak, standing aside",
    "vol spike # normal": "volatility spike",
    "below # contract": "one contract is more than the risk budget",
    "daily loss limit": "daily loss limit reached",
    "rate limit #/min": "too many trades this minute",
    "session trade cap #": "daily trade cap reached",
}
