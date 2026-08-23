"""PumpPortal (pumpportal.fun) — a free, keyless third-party WebSocket feed
built on the same on-chain pump.fun program events pumpfun_live.py already
watches via raw Solana RPC.

This module does THREE jobs now, not two:

1. BUYER DIVERSITY per candidate mint (the original purpose). The single
   most-cited real-scalper signal the strategy was missing is not "how much
   SOL has this token raised" but "how many DISTINCT wallets are buying
   it." A coin that racks up SOL fast from ONE wallet (or a handful funded
   from the same source in the same block) is the textbook
   bundled/insider/sniper-launch pattern, not organic demand — the old
   scoring had no way to tell the two apart. PumpPortal's
   subscribeTokenTrade stream gives per-trade buyer addresses in real
   time, which is exactly what's needed to count that.

2. NEW-COIN DETECTION (added after live testing exposed a real gap in
   pumpfun_live.py). That RPC-based feed subscribes to every transaction
   mentioning the pump.fun program — creates, buys, AND sells — but can
   only afford ~3 getTransaction calls/sec (see PUMPFUN_LIVE_MIN_TX_INTERVAL_S
   in pumpfun_live.py) to avoid tripping Helius's rate limit. Given
   pump.fun's real volume, that 3/sec budget is mostly consumed by
   unrelated buy/sell traffic, and creates are a small fraction of even
   that — meaning the RPC feed likely misses the large majority of actual
   new coins, especially during active periods. PumpPortal's
   subscribeNewToken stream gets every creation event directly, with NO
   rate limit and NO follow-up RPC call, because PumpPortal pushes the
   event to every subscriber itself. recent_new_coins() exposes that as a
   candidate source the autotrade cycle merges alongside
   pumpfun_live.LiveFeed's detections — not a replacement, since that RPC
   feed is what's actually been verified end-to-end; this fills its
   detection gap rather than trusting an unverified feed alone.

3. LIVE PRICE for currently-held positions (added after a live position
   was gapped past its trailing-stop zone straight into a much worse
   stop-loss between two 10-second polls). Every subscribeTokenTrade
   message -- buy AND sell -- carries `marketCapSol`, the token's market
   cap in SOL at that instant: a free, push-based price tick with no
   per-request cost, since the WebSocket is already open for buyer-
   diversity tracking. `pin()`/`last_market_cap_sol()` expose this for a
   held position specifically -- see pin()'s docstring for why a normal
   tracked mint isn't enough (it expires after PUMPPORTAL_WATCH_TTL_S,
   which a real hold routinely outlives). run_exit_check() prefers this
   over the slower DexScreener poll when it's available and falls back
   silently when it isn't -- same "missing feed never blocks a trade"
   rule as everywhere else in this file. This module still never touches
   a private key or places a trade; it only supplies a price.

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
   read-only market data — but it is a real usage cap). PUMPFUN_MAX_WATCHED_MINTS
   and PUMPPORTAL_WATCH_TTL_S bound how many mints are tracked and for how
   long, and expired mints are explicitly unsubscribed, so exposure stays
   bounded rather than growing for as long as the process runs — but the
   right values genuinely depend on real-world pump.fun creation volume
   this dev environment cannot observe. Live testing found the initial
   guess (50 mints, 10 min) far too small for the actual rate (a new coin
   roughly every 1-2 seconds even in a quiet moment); the current defaults
   (300, 3 min) are a better-informed second guess, not a verified answer
   — tune via env if `pp-watch` still shows lots of "no PumpPortal record".
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"
# Real observed pump.fun creation volume is far higher than a first guess —
# live testing showed a new coin roughly every 1-2 seconds even in a quiet
# moment. A too-small cap fills almost immediately and, combined with a long
# TTL, stays clogged with old entries — meaning a freshly detected candidate
# has real odds of never getting a tracking slot at all, showing up as "no
# PumpPortal record" for reasons that have nothing to do with the mint
# itself. Both are env-configurable since the right values depend on
# real-world volume this dev environment can't observe (no network access).
DEFAULT_MAX_WATCHED_MINTS = 300
DEFAULT_WATCH_TTL_S = 180.0   # our own scoring only cares about the first few minutes anyway
_RECONNECT_BACKOFF_S = (2, 5, 10, 30, 60)
_PING_TIMEOUT_S = 60   # see pumpfun_live.py — websockets' 20s default is too tight here


def ws_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("PUMPPORTAL_WS_URL") or PUMPPORTAL_WS_URL).strip()


def max_watched_mints(env: Optional[dict] = None) -> int:
    e = env if env is not None else os.environ
    try:
        return max(1, int(e.get("PUMPPORTAL_MAX_WATCHED_MINTS", DEFAULT_MAX_WATCHED_MINTS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_WATCHED_MINTS


def watch_ttl_s(env: Optional[dict] = None) -> float:
    e = env if env is not None else os.environ
    try:
        return max(1.0, float(e.get("PUMPPORTAL_WATCH_TTL_S", DEFAULT_WATCH_TTL_S)))
    except (TypeError, ValueError):
        return DEFAULT_WATCH_TTL_S


class PumpPortalFeed:
    """Tracks per-mint buyer diversity, new-coin creations, AND live price
    for pinned (held) positions via PumpPortal's free WebSocket feed.
    Purely observational — never touches a private key, never places a
    trade. buyer_stats()/recent_new_coins()/last_market_cap_sol()/status()
    are the non-blocking read side other code polls; they never touch the
    network themselves."""

    def __init__(self, env: Optional[dict] = None):
        self._env = env
        self._lock = threading.Lock()
        # mint -> {"buyers": set, "buy_count": int, "first_seen": float,
        #          "pinned": bool, "last_market_cap_sol": float|None,
        #          "last_trade_at": float|None}
        self._watched: dict[str, dict] = {}
        self._needs_resubscribe = False
        self._status: dict = {"connected": False, "mints_tracked": 0, "trades_seen": 0,
                              "creations_seen": 0, "last_error": None, "started_at": None}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._max_watched_mints = max_watched_mints(env)
        self._watch_ttl_s = watch_ttl_s(env)
        self._recent_creations: deque = deque(maxlen=self._max_watched_mints)

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

    def _record_creation(self, mint: str, msg: dict) -> None:
        """Record a brand-new mint as a scoring candidate. Deliberately
        conservative about what's extracted from PumpPortal's create
        message: symbol/name are low-risk (display only, never scored),
        but market_cap_usd/price_usd/has_social_links are left None here
        rather than guessing at PumpPortal's numeric field semantics —
        run_autotrade_cycle's existing enrichment fetch
        (pumpfun_data.get_coin(), already built and verified live) fills
        those in from pump.fun's own API when scoring, the same as it
        already does for pumpfun_live.py's bare-address detections."""
        coin = {"address": mint, "symbol": msg.get("symbol"), "name": msg.get("name"),
               "created_at_ms": int(time.time() * 1000), "market_cap_usd": None,
               "price_usd": None, "has_social_links": None, "sol_raised": None,
               "migrated": False, "source": "pumpportal"}
        with self._lock:
            self._recent_creations.append(coin)
            self._status["creations_seen"] += 1

    def recent_new_coins(self, limit: int = 30) -> list[dict]:
        with self._lock:
            return list(self._recent_creations)[-limit:][::-1]   # newest first

    def status(self) -> dict:
        with self._lock:
            st = dict(self._status)
            st["mints_tracked"] = len(self._watched)
            return st

    def _track(self, mint: str) -> bool:
        """Start tracking a newly-created mint. Returns True if it should be
        subscribed to trade events (new to us and under the tracking cap)."""
        with self._lock:
            if mint in self._watched or len(self._watched) >= self._max_watched_mints:
                return False
            self._watched[mint] = {"buyers": set(), "buy_count": 0, "first_seen": time.time(),
                                   "pinned": False, "last_market_cap_sol": None,
                                   "last_trade_at": None}
            return True

    def pin(self, mint: str) -> None:
        """Guarantee `mint` keeps receiving trade events for as long as it's
        held, regardless of the normal creation-tracking cap or
        PUMPPORTAL_WATCH_TTL_S (default 180s) -- a scalp position is very
        often held longer than that TTL, and silently losing live price
        updates partway through a hold would fall back to the much
        coarser/slower DexScreener poll with no visible warning. Idempotent
        and cheap; call it every exit-check tick for every held position.
        A pinned mint is exempt from the tracking cap on purpose: capacity
        limits exist to bound how many CANDIDATES get watched, never to
        risk dropping a position real money is already in."""
        with self._lock:
            w = self._watched.get(mint)
            if w is None:
                self._watched[mint] = {"buyers": set(), "buy_count": 0, "first_seen": time.time(),
                                       "pinned": True, "last_market_cap_sol": None,
                                       "last_trade_at": None}
                self._needs_resubscribe = True
            elif not w.get("pinned"):
                w["pinned"] = True

    def unpin(self, mint: str) -> None:
        """Release the pin once a position is fully closed -- the mint then
        ages out through the normal TTL like any other tracked candidate,
        instead of being watched forever."""
        with self._lock:
            w = self._watched.get(mint)
            if w is not None:
                w["pinned"] = False

    def last_market_cap_sol(self, mint: str) -> Optional[dict]:
        """The most recent trade's marketCapSol for `mint`, with its age --
        None if this mint has never traded since we started watching it
        (including: never pinned/tracked at all). Callers should treat a
        stale reading (a large age_s) the same as no reading: this only
        ticks on real trade activity, so a currently-illiquid mint can
        legitimately go quiet for a while."""
        with self._lock:
            w = self._watched.get(mint)
            if w is None or w.get("last_market_cap_sol") is None:
                return None
            return {"market_cap_sol": w["last_market_cap_sol"],
                   "age_s": max(0.0, time.time() - w["last_trade_at"])}

    def _record_price(self, mint: str, market_cap_sol) -> None:
        if market_cap_sol is None:
            return
        try:
            market_cap_sol = float(market_cap_sol)
        except (TypeError, ValueError):
            return
        with self._lock:
            w = self._watched.get(mint)
            if w is None:
                return
            w["last_market_cap_sol"] = market_cap_sol
            w["last_trade_at"] = time.time()

    def _pop_needs_resubscribe(self) -> bool:
        with self._lock:
            flag = self._needs_resubscribe
            self._needs_resubscribe = False
            return flag

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
            expired = [m for m, w in self._watched.items()
                      if not w.get("pinned") and now - w["first_seen"] > self._watch_ttl_s]
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
                expired = self._pop_expired()
                # A pin() call from another thread (run_exit_check, adding a
                # held position that wasn't already tracked) sets this flag
                # so the new mint gets subscribed on the next message here,
                # rather than waiting for the next create/expiry event to
                # incidentally trigger a resend.
                needs_resub = self._pop_needs_resubscribe()
                if expired:
                    for mint in expired:
                        try:
                            await ws.send(json.dumps({"method": "unsubscribeTokenTrade",
                                                      "keys": [mint]}))
                        except Exception:  # noqa: BLE001 — best-effort, never fatal
                            pass
                if expired or needs_resub:
                    # Belt-and-suspenders against the same additive-vs-replace
                    # ambiguity noted in _handle_message: if subscribeTokenTrade
                    # actually replaces the server-side key list rather than
                    # adding to it, an explicit per-mint unsubscribe may not be
                    # honored the way expected either. Resending the current
                    # (post-prune) full list keeps us correctly subscribed to
                    # exactly what we still track, regardless of which
                    # semantics PumpPortal actually implements.
                    remaining = self.tracked_mints()
                    if remaining:
                        try:
                            await ws.send(json.dumps({"method": "subscribeTokenTrade",
                                                      "keys": remaining}))
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
                self._record_creation(mint, msg)
                # Resend the FULL current watch list, not just the new mint.
                # PumpPortal's docs don't make it unambiguous whether repeated
                # subscribeTokenTrade calls are additive or replace the prior
                # key list — live testing showed near-zero buy events ever
                # recorded across many tracked mints despite high real
                # trading volume, exactly the symptom of a replace-based API
                # silently dropping every previously-watched mint each time a
                # new one (created every 1-2 seconds) gets subscribed. Always
                # sending the complete set is correct either way: harmless
                # if additive, required if not.
                try:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade",
                                              "keys": self.tracked_mints()}))
                except Exception:  # noqa: BLE001 — best-effort, never fatal
                    pass
        elif tx_type in ("buy", "sell"):
            if tx_type == "buy":
                self._record_buy(mint, msg.get("traderPublicKey"))
            # Every trade -- buy or sell -- carries marketCapSol, a live
            # price tick regardless of direction; a held position's price
            # should update on a sell just as much as a buy.
            self._record_price(mint, msg.get("marketCapSol"))
