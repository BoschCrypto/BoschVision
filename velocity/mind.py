"""The live "mind" view: what every agent is thinking, bar by bar.

Three pieces:

  FrameBuilder   turns the engine's state after a bar into one flat record
                 (votes, the readings behind them, the arbiter's score, the
                 warden's stance, position, P&L, and the events that just
                 happened between agents)
  MindHub        holds recent frames for any number of open browser tabs
  serve()        a small local web server: the page polls it once a second

Two sources drive it. `run_live` warms the agents on recent one-minute
candles, then reads each new candle as it closes and shows the last traded
price in between. `run_replay` plays a CSV through the same pipe.

Everything here is PAPER trading. No order leaves this process.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from .engine import Engine
from .types import NS_PER_S, Decision, Tick

_Q = re.compile(r"trend=([-+\d.]+)atr roc=([-+\d.]+) z=([-+\d.]+) rsi=(\d+)")
_C = re.compile(r"struct=([-+\d.]+) brk=([-+\d.]+)( TRAP)? vwap=([-+\d.]+)atr comp=([\d.]+)")
_NUM = re.compile(r"[-+]?[\d,]*\.?\d+x?")
_WEB = os.path.join(os.path.dirname(__file__), "web")

_BLOCK_WORDS = [
    ("cooldown", "cooling down after the last exit"),
    ("losses in a row", "losing streak, standing aside"),
    ("vol spike", "volatility spike, standing down"),
    ("below", "one contract is more than the risk budget"),
    ("daily loss", "daily loss limit reached"),
    ("drawdown", "drawdown brake on"),
    ("rate limit", "too many trades this minute"),
    ("session trade cap", "daily trade cap reached"),
    ("KILL", "kill switch is on"),
    ("halted", "halted for the day"),
    ("spread", "spread too wide"),
]


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / NS_PER_S, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x: float, n: int = 3) -> float:
    return round(float(x), n)


class FrameBuilder:
    """One flat dict per bar. Keys are short because thousands of these are
    shipped to the browser; `FRAME_KEYS` documents them."""

    def __init__(self, engine: Engine, on_close: Optional[Callable] = None) -> None:
        self.eng = engine
        self.on_close = on_close
        self.base_trades = 0
        self._n_trades = len(engine.executor.trades)
        self._last_block = ""
        self._last_news = ""
        self.base_pnl = 0.0

    def frame(self, tick: Tick, d: Decision) -> dict:
        eng, bb = self.eng, self.eng.bb
        now = tick.ts_ns
        ev: list[list[str]] = []
        f: dict = {"t": _iso(now), "px": _r(tick.price, 2)}

        q = bb.get("QUANT")
        f["qv"] = _r(bb.read("QUANT", now))
        f["qf"] = _r(q.confidence if q else 0.0, 2)
        m = _Q.search(q.reason) if q else None
        f["qt"], f["qr"], f["qz"], f["qi"] = ((float(m.group(1)), float(m.group(2)),
                                               float(m.group(3)), int(m.group(4))) if m else (0.0, 0.0, 0.0, 50))

        c = bb.get("CHARTIST")
        f["cv"] = _r(bb.read("CHARTIST", now))
        f["cf"] = _r(c.confidence if c else 0.0, 2)
        m = _C.search(c.reason) if c else None
        f["cs"], f["cb"], f["cw"], f["cc"] = ((float(m.group(1)), float(m.group(2)),
                                               float(m.group(4)), float(m.group(5))) if m else (0.0, 0.0, 0.0, 0.0))
        ch = eng.chartist
        f["kh"] = _r(getattr(ch, "channel_high", 0.0), 2)
        f["kl"] = _r(getattr(ch, "channel_low", 0.0), 2)
        if getattr(ch, "last_event", None):
            ev.append(list(ch.last_event))
        elif hasattr(ch, "last_event"):
            pass                                   # channel rule, nothing happened this bar
        elif ch._since_break == 0:
            way = "up" if ch._break_dir > 0 else "down"
            if m and m.group(3):
                ev.append(["trap", f"CHARTIST calls the break {way} a trap and fades it"])
            elif f["qv"] * ch._break_dir > 0.02:
                ev.append(["break", f"CHARTIST confirms a break {way}: QUANT's trend agrees, full weight"])
            else:
                ev.append(["counter", f"CHARTIST halves a break {way}: it runs against QUANT's trend"])

        n = bb.get("SENTINEL")
        live = bool(n and n.alive(now) and n.confidence > 0)
        f["nv"] = _r(bb.read("SENTINEL", now)) if live else 0.0
        f["nl"] = 1 if live else 0
        sen = eng.sentinel
        f["nh"] = sen.read_count if sen is not None else 0
        f["nx"] = sen.latest[0][:110] if sen is not None else ""
        if sen is not None and sen.latest[0] and sen.latest[0] != self._last_news:
            self._last_news = sen.latest[0]
            tone = "bullish" if sen.latest[1] > 0 else "bearish"
            ev.append(["news", f"SENTINEL reads {tone} news ({sen.latest[1]:+.2f}): {sen.latest[0][:90]}"])

        a = bb.get("ARBITER")
        f["as"] = _r(eng.arbiter._last_score)
        ma = re.search(r"agree=(\d+) disp=([\d.]+)", a.reason) if a else None
        f["ag"], f["ad"] = (int(ma.group(1)), float(ma.group(2))) if ma else (0, 0.0)

        w = bb.get("WARDEN")
        f["wa"] = _r(w.confidence if w else 1.0, 2)

        # what the arbiter decided on this bar
        r = d.reason
        block = ""
        if d.is_trade:
            side = "LONG" if d.side > 0 else "SHORT"
            if getattr(eng, "watch_only", False):
                ev.append(["wait", f"ARBITER would open {side} at score {d.score:+.2f} "
                                   f"(watching only, no position taken)"])
            else:
                ev.append(["open", f"ARBITER opens {side} at score {d.score:+.2f}; "
                                   f"WARDEN approved ${d.notional:,.0f}"])
        elif r.startswith("WARDEN veto"):
            raw = r.split(":", 1)[1]
            block = next((words for key, words in _BLOCK_WORDS if key in raw), _NUM.sub("#", raw).strip())
            if block != self._last_block:
                ev.append(["veto", f"WARDEN refuses the entry: {block}"])
        elif r.startswith("SENTINEL veto"):
            block = "news against the trade"
            if block != self._last_block:
                ev.append(["veto", "SENTINEL vetoes the entry: news points the other way"])
        elif r.startswith("no confluence"):
            block = "only one agent behind the score"
            if block != self._last_block:
                ev.append(["wait", "ARBITER waits: score is high enough but only one agent is behind it"])
        elif r.startswith("below minimum size"):
            block = "votes too far apart"
            if block != self._last_block:
                ev.append(["wait", "ARBITER drops the entry: the two votes are too far apart"])
        self._last_block = block
        f["wb"] = block

        trades = eng.executor.trades
        for t in trades[self._n_trades:]:
            side = "LONG" if t.side > 0 else "SHORT"
            how = {"target": "target reached", "stop": "stopped out"}.get(
                t.reason_out, "signal flipped" if "flip" in t.reason_out else
                getattr(eng.chartist, "exit_label", "signal faded") if t.reason_out.startswith("exit") else
                "WARDEN flattened it: " + t.reason_out.split(": ", 1)[-1]
                if t.reason_out.startswith("risk flatten") else t.reason_out)
            ev.append(["win" if t.pnl > 0 else "loss", f"Closed {side} {t.pnl:+.2f} ({how})"])
            if self.on_close is not None:
                self.on_close(t)
        self._n_trades = len(trades)

        f["pos"] = bb.open_side
        f["sz"] = _r(bb.open_notional / bb.open_entry, 4) if bb.open_entry else 0.0
        f["en"] = _r(bb.open_entry, 2)
        f["up"] = _r(bb.unrealised(tick.price), 2)
        f["pnl"] = _r(bb.realised_pnl - self.base_pnl, 2)
        f["nt"] = len(trades) - self.base_trades
        f["ev"] = ev
        return f


FRAME_KEYS = {
    "t": "bar close, UTC", "px": "price",
    "qv": "QUANT vote", "qf": "QUANT confidence", "qt": "trend, ATRs", "qr": "momentum, ATRs",
    "qz": "stretch, z-score", "qi": "RSI",
    "cv": "CHARTIST vote", "cf": "CHARTIST confidence", "cs": "structure", "cb": "break score",
    "cw": "distance from VWAP, ATRs", "cc": "compression",
    "kh": "channel: a close at or above this goes long", "kl": "channel: a close at or below this goes flat",
    "nv": "SENTINEL vote", "nl": "news live (1/0)", "nh": "headlines read", "nx": "latest headline that scored",
    "as": "ARBITER score", "ag": "agents agreeing", "ad": "vote split",
    "wa": "WARDEN risk appetite", "wb": "why the entry is blocked, if it is",
    "pos": "position (-1/0/1)", "sz": "position size, units of the underlying", "en": "entry price", "up": "open P&L", "pnl": "closed P&L",
    "nt": "trades closed", "ev": "events on this bar",
}


class TradeLog:
    """Append each closed trade to a CSV. A paper run you cannot review
    afterwards teaches nothing, and the in-memory book is gone on restart."""

    COLS = ["closed_utc", "opened_utc", "symbol", "side", "qty", "entry", "exit",
            "held_min", "fees", "net", "closed_by"]

    def __init__(self, path: str, symbol: str) -> None:
        self.path = path
        self.symbol = symbol

    def __call__(self, t) -> None:
        new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        with open(self.path, "a", newline="") as fh:
            if new:
                fh.write(",".join(self.COLS) + "\n")
            fh.write(",".join(str(x) for x in [
                _iso(t.exit_ns), _iso(t.entry_ns), self.symbol, "long" if t.side > 0 else "short",
                round(t.qty, 6), round(t.entry, 2), round(t.exit, 2), round(t.held_s / 60),
                round(t.costs, 4), round(t.pnl, 4), t.reason_out.replace(",", ";")]) + "\n")


# --------------------------------------------------------------------- hub

class MindHub:
    """Recent frames for the browser. Thread-safe; the engine thread
    publishes and any number of request threads read."""

    def __init__(self, meta: dict, keep: int = 3000) -> None:
        self.meta = dict(meta)
        self._lock = threading.Lock()
        self._frames: deque[tuple[int, dict]] = deque(maxlen=keep)
        self._seq = 0
        self._px: dict = {}
        self._status = "starting"

    def publish(self, frame: dict) -> None:
        with self._lock:
            self._seq += 1
            self._frames.append((self._seq, frame))

    def price(self, px: float, next_in: float) -> None:
        with self._lock:
            self._px = {"px": round(px, 2), "next": max(0, round(next_in)), "at": time.time()}

    def status(self, text: str) -> None:
        with self._lock:
            self._status = text

    def poll(self, since: int, limit: int = 600) -> dict:
        with self._lock:
            new = [(s, f) for s, f in self._frames if s > since][-limit:]
            px = dict(self._px)
            if px:
                # how old the last price is, so the page can say when the
                # feed has gone quiet instead of showing a frozen number
                px["age"] = round(time.time() - px.pop("at"), 1)
            return {"seq": self._seq, "frames": [f for _s, f in new], "px": px,
                    "status": self._status, "meta": self.meta}


# ------------------------------------------------------------------ server

def _handler(hub: MindHub):
    files = {"/": ("index.html", "text/html; charset=utf-8"),
             "/mind.js": ("mind.js", "application/javascript; charset=utf-8"),
             "/mind.css": ("mind.css", "text/css; charset=utf-8")}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            return

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/poll":
                try:
                    since = int(parse_qs(u.query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                self._send(200, json.dumps(hub.poll(since), separators=(",", ":")).encode(),
                           "application/json")
            elif u.path in files:
                name, ctype = files[u.path]
                with open(os.path.join(_WEB, name), "rb") as fh:
                    self._send(200, fh.read(), ctype)
            else:
                self._send(404, b"not found", "text/plain")

    return H


def serve(hub: MindHub, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), _handler(hub))
    threading.Thread(target=srv.serve_forever, daemon=True, name="velocity-mind-http").start()
    return srv


# ----------------------------------------------------------------- sources

def run_replay(hub: MindHub, engine: Engine, ticks: Iterable[Tick], bars_per_s: float = 10.0,
               stop: Optional[threading.Event] = None) -> list[dict]:
    """Play ticks through the engine. bars_per_s <= 0 runs flat out (export)."""
    fb = FrameBuilder(engine)
    out: list[dict] = []
    delay = 1.0 / bars_per_s if bars_per_s > 0 else 0.0
    hub.status("replay")

    def see(tick: Tick, d: Decision) -> None:
        f = fb.frame(tick, d)
        out.append(f)
        hub.publish(f)
        if delay:
            (stop.wait(delay) if stop else time.sleep(delay))
        if stop is not None and stop.is_set():
            raise KeyboardInterrupt

    try:
        engine.run(ticks, observer=see)
    except KeyboardInterrupt:
        pass
    hub.status("replay finished")
    return out


def run_live(hub: MindHub, engine: Engine, symbol: str, stop: threading.Event,
             poll_s: float = 2.0, warm_bars: int = 1800,
             fetch_rows: Optional[Callable] = None, last_price: Optional[Callable] = None,
             now: Callable[[], float] = time.time,
             on_close: Optional[Callable] = None, sentinel=None, bar_s: int = 60) -> None:
    """Warm up on recent bars, then follow the market bar by bar. `bar_s` is
    the bar length in seconds; `fetch_rows(symbol, n)` returns the last n
    bars of that length."""
    from .feeds import coinbase_history as cb
    if fetch_rows is None:
        fetch_rows = (cb.fetch_rows if bar_s == 60 else
                      (lambda sym, n: cb.fetch_bars(sym, bar_s, n)))
    last_price = last_price or cb.last_price
    fb = FrameBuilder(engine, on_close=on_close)
    bb = engine.bb
    last_ts = 0

    def feed(ts: int, close: float, vol: float) -> dict:
        tick = Tick(ts * NS_PER_S, symbol, close, vol)
        return fb.frame(tick, engine.on_tick(tick))

    # Warm-up primes the indicators on history and must not trade: a position
    # or P&L from candles that closed before you opened the page is not this
    # session's. The book is held halted until the last historical bar is in.
    hub.status(f"warming up on {warm_bars} bars of history")
    bb.halt("warm-up")
    cur = int(now()) // bar_s * bar_s
    rows = [r for r in fetch_rows(symbol, warm_bars) if r[0] < cur]
    for ts, close, vol in rows:
        f = feed(ts, close, vol)
        last_ts = ts
        f["warm"] = 1
        f["ev"] = []
        f["wb"] = ""
        hub.publish(f)
    bb.halted = False
    bb.halt_reason = ""
    engine.vetoes.clear()
    fb._last_block = ""
    fb._n_trades = fb.base_trades = len(engine.executor.trades)
    fb.base_pnl = bb.realised_pnl
    hub.meta["live_since"] = _iso(int(now()) * NS_PER_S)
    # News starts only now: today's headlines have no business colouring
    # yesterday's warm-up bars.
    if sentinel is not None:
        engine.sentinel = sentinel
        engine.start_slow_lane()
    hub.status("live")

    while not stop.is_set():
        t = now()
        cur = int(t) // bar_s * bar_s
        try:
            # a candle is final a few seconds after its bar ends
            if cur - bar_s > last_ts and t - cur >= 4:
                # Ask for every bar since the last one we fed, not a fixed
                # five: after an outage the missing bars must be replayed in
                # order, or the agents see time jump and stops go unchecked.
                need = min(warm_bars, (cur - last_ts) // bar_s + 2)
                for ts, close, vol in fetch_rows(symbol, need):
                    if last_ts < ts < cur:
                        hub.publish(feed(ts, close, vol))
                        last_ts = ts
            hub.price(last_price(symbol), cur + bar_s + 4 - t if cur - bar_s <= last_ts else 0)
            hub.status("live")
        except Exception as e:  # network trouble must not end the session
            hub.status(f"feed trouble, retrying: {type(e).__name__}")
        stop.wait(poll_s)
    engine.stop_slow_lane()
