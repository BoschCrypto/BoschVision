"""PumpPortal (pumpportal.fun) — a free, keyless third-party WebSocket feed
built on the same on-chain pump.fun program events pumpfun_live.py already
watches via raw Solana RPC.

This module does NOT replace pumpfun_live.py as the detection source —
that feed is what was verified working end-to-end against a real Helius
connection, and stays the primary mechanism the autotrade cycle actually
acts on. What this module adds instead: BUYER DIVERSITY per candidate mint.

Why this matters, concretely: the single most-cited real-scalper signal the
strategy was missing is not "how much SOL has this token raised" (the
existing signal) but "how many DISTINCT wallets are buying it." A coin that
racks up SOL fast from ONE wallet (or a handful funded from the same source
in the same block) is the textbook bundled/insider/sniper-launch pattern,
not organic demand — and the old scoring had no way to tell the two apart.
PumpPortal's subscribeTokenTrade stream gives per-trade buyer addresses in
real time, which is exactly what's needed to count that.

Honesty notes, stated plainly rather than glossed over:
1. PumpPortal is an unofficial third-party service (not Solana Foundation,
   not pump.fun itself) built specifically for bots in this ecosystem. Its
   new-token and trade subscriptions are documented as free and keyless,
   but that could change without notice — the same class of risk as
   pumpfun_data.py's REST polling.
2. The exact WebSocket message/field names below are implemented from
   public documentation, NOT verified against a live connection from this
   development environment (which cannot reach external WebSocket hosts at
   all). `hf-bot memecoin pp-watch` exists to verify this actually works,
   with real output on your machine, before the buyer-diversity signal is
   ever trusted inside scoring.
3. This is a SUPPLEMENTARY, best-effort signal. If PumpPortal is
   unreachable, or a mint has no buyer-diversity record yet, scoring falls
   back to freshness + SOL-raised alone (see memecoin_strategy.py) — a
   missing PumpPortal connection must never block a trade, only remove one
   input to it.
4. subscribeTokenTrade is metered past a small free allowance per
   PumpPortal's own docs (this file doesn't spend real money either way —
   read-only market data — but it is a real usage cap). _MAX_WATCHED_MINTS
   bounds how many mints are tracked at once, and expired mints are
   explicitly unsubscribed, so exposure stays bounded rather than growing
   for as long as the process runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"
_MAX_WATCHED_MINTS = 50
_WATCH_TTL_S = 600.0   # stop tracking a mint 10 min after we first saw it created
_RECONNECT_BACKOFF_S = (2, 5, 10, 30, 60)
_PING_TIMEOUT_S = 60   # see pumpfun_live.py — websockets' 20s default is too tight here


def ws_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("PUMPPORTAL_WS_URL") or PUMPPORTAL_WS_URL).strip()


class PumpPortalFeed:
    """Tracks per-mint buyer diversity via PumpPortal's free WebSocket feed.
    Purely observational — never touches a private key, never places a
    trade. buyer_stats()/status() are the non-blocking read side other code
    polls; they never touch the network themselves."""

    def __init__(self, env: Optional[dict] = None):
        self._env = env
        self._lock = threading.Lock()
        self._watched: dict[str, dict] = {}   # mint -> {"buyers": set, "buy_count": int, "first_seen": float}
        self._status: dict = {"connected": False, "mints_tracked": 0, "trades_seen": 0,
                              "last_error": None, "started_at": None}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            self._status["started_at"] = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def buyer_stats(self, mint: str) -> Optional[dict]:
        with self._lock:
            w = self._watched.get(mint)
            if not w:
                return None
            return {"unique_buyers": len(w["buyers"]), "buy_count": w["buy_count"],
                   "age_s": max(0.0, time.time() - w["first_seen"])}

    def tracked_mints(self) -> list[str]:
        with self._lock:
            return list(self._watched.keys())

    def status(self) -> dict:
        with self._lock:
            st = dict(self._status)
            st["mints_tracked"] = len(self._watched)
            return st

    def _track(self, mint: str) -> bool:
        """Start tracking a newly-created mint. Returns True if it should be
        subscribed to trade events (new to us and under the tracking cap)."""
        with self._lock:
            if mint in self._watched or len(self._watched) >= _MAX_WATCHED_MINTS:
                return False
            self._watched[mint] = {"buyers": set(), "buy_count": 0, "first_seen": time.time()}
            return True

    def _record_buy(self, mint: str, trader: Optional[str]) -> None:
        with self._lock:
            w = self._watched.get(mint)
            if w is None:
                return
            w["buy_count"] += 1
            if trader:
                w["buyers"].add(trader)
            self._status["trades_seen"] += 1

    def _pop_expired(self) -> list[str]:
        now = time.time()
        with self._lock:
            expired = [m for m, w in self._watched.items() if now - w["first_seen"] > _WATCH_TTL_S]
            for m in expired:
                del self._watched[m]
        return expired

    def _run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                asyncio.run(self._subscribe_once())
                attempt = 0   # a clean disconnect resets backoff
            except Exception as e:  # noqa: BLE001 — the loop must never die
                with self._lock:
                    self._status["connected"] = False
                    self._status["last_error"] = f"{type(e).__name__}: {e}"
            if self._stop.is_set():
                return
            delay = _RECONNECT_BACKOFF_S[min(attempt, len(_RECONNECT_BACKOFF_S) - 1)]
            attempt += 1
            time.sleep(delay)

    async def _subscribe_once(self) -> None:
        import websockets

        url = ws_url(self._env)
        loop = asyncio.get_running_loop()
        async with websockets.connect(url, ping_timeout=_PING_TIMEOUT_S) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            with self._lock:
                self._status["connected"] = True
                self._status["last_error"] = None
            async for raw in ws:
                if self._stop.is_set():
                    return
                for mint in self._pop_expired():
                    try:
                        await ws.send(json.dumps({"method": "unsubscribeTokenTrade",
                                                  "keys": [mint]}))
                    except Exception:  # noqa: BLE001 — best-effort, never fatal
                        pass
                # Parsing/dispatch is pure Python (no network), so unlike
                # pumpfun_live.py's getTransaction calls there's nothing here
                # worth moving to an executor — this can't stall the loop.
                await self._handle_message(raw, ws)

    async def _handle_message(self, raw: str, ws) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        mint = msg.get("mint")
        if not mint:
            return
        tx_type = (msg.get("txType") or "").lower()
        if tx_type == "create":
            if self._track(mint):
                try:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                except Exception:  # noqa: BLE001 — best-effort, never fatal
                    pass
        elif tx_type == "buy":
            self._record_buy(mint, msg.get("traderPublicKey"))
