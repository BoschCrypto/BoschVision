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

    def __init__(self, engine: Engine) -> None:
        self.eng = engine
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
        if ch._since_break == 0:
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
        if live and n.reason != self._last_news:
            self._last_news = n.reason
            ev.append(["news", f"SENTINEL: {n.reason[:80]}"])

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
            ev.append(["open", f"ARBITER opens {side} at score {d.score:+.2f}; WARDEN approved ${d.notional:,.0f}"])
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
                "signal faded" if t.reason_out.startswith("exit") else t.reason_out)
            ev.append(["win" if t.pnl > 0 else "loss", f"Closed {side} {t.pnl:+.2f} ({how})"])
        self._n_trades = len(trades)

        f["pos"] = bb.open_side
        f["en"] = _r(bb.open_entry, 2)
        f["up"] = _r(bb.unrealised(tick.price), 2)
        f["pnl"] = _r(bb.realised_pnl - self.base_pnl, 2)
        f["nt"] = len(trades)
        f["ev"] = ev
        return f


FRAME_KEYS = {
    "t": "bar close, UTC", "px": "price",
    "qv": "QUANT vote", "qf": "QUANT confidence", "qt": "trend, ATRs", "qr": "momentum, ATRs",
    "qz": "stretch, z-score", "qi": "RSI",
    "cv": "CHARTIST vote", "cf": "CHARTIST confidence", "cs": "structure", "cb": "break score",
    "cw": "distance from VWAP, ATRs", "cc": "compression",
    "nv": "SENTINEL vote", "nl": "news live (1/0)",
    "as": "ARBITER score", "ag": "agents agreeing", "ad": "vote split",
    "wa": "WARDEN risk appetite", "wb": "why the entry is blocked, if it is",
    "pos": "position (-1/0/1)", "en": "entry price", "up": "open P&L", "pnl": "closed P&L",
    "nt": "trades closed", "ev": "events on this bar",
}


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
            return {"seq": self._seq, "frames": [f for _s, f in new], "px": dict(self._px),
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
             now: Callable[[], float] = time.time) -> None:
    """Warm up on recent candles, then follow the market bar by bar."""
    from .feeds import coinbase_history as cb
    fetch_rows = fetch_rows or cb.fetch_rows
    last_price = last_price or cb.last_price
    fb = FrameBuilder(engine)
    bb = engine.bb
    last_ts = 0

    def feed(ts: int, close: float, vol: float) -> dict:
        tick = Tick(ts * NS_PER_S, symbol, close, vol)
        return fb.frame(tick, engine.on_tick(tick))

    # Warm-up primes the indicators on history and must not trade: a position
    # or P&L from candles that closed before you opened the page is not this
    # session's. The book is held halted until the last historical bar is in.
    hub.status(f"warming up on {warm_bars} minutes of history")
    bb.halt("warm-up")
    cur = int(now()) // 60 * 60
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
    fb._n_trades = len(engine.executor.trades)
    fb.base_pnl = bb.realised_pnl
    hub.meta["live_since"] = _iso(int(now()) * NS_PER_S)
    hub.status("live")

    while not stop.is_set():
        t = now()
        cur = int(t) // 60 * 60
        try:
            # a candle is final a few seconds after its minute ends
            if cur - 60 > last_ts and t - cur >= 4:
                for ts, close, vol in fetch_rows(symbol, 5):
                    if last_ts < ts < cur:
                        hub.publish(feed(ts, close, vol))
                        last_ts = ts
            hub.price(last_price(symbol), cur + 64 - t if cur - 60 <= last_ts else 0)
            hub.status("live")
        except Exception as e:  # network trouble must not end the session
            hub.status(f"feed trouble, retrying: {type(e).__name__}")
        stop.wait(poll_s)
