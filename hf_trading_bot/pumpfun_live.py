"""Real-time pump.fun new-token detection via a persistent Solana WebSocket
subscription — not polling. This is what "connected to a live feed" actually
means, and it's a materially different thing from pumpfun_data.py's REST
polling: sub-second detection the instant a token is created on-chain,
instead of whatever happened to be new the last time a poll fired. This is
the mechanism Photon's Memescope-speed bots actually use.

How it works: subscribe to Solana program logs mentioning the pump.fun
program. For every matching (successful) transaction, fetch its full detail
and look for a brand-new SPL token mint appearing in that transaction's
token-balance changes (present in postTokenBalances, absent from
preTokenBalances). That is the new token.

Two things stated plainly rather than glossed over:

1. The detection heuristic is inferred from stable, DOCUMENTED Solana RPC
   semantics (pre/post token balances on a transaction) — not by parsing
   pump.fun's own undocumented instruction format or log strings, which
   would be far more fragile. It should catch essentially every pump.fun
   token creation; a false positive (something unrelated creating a token in
   a transaction that also happens to touch the pump.fun program) is
   possible but rare.
2. The websocket subscription API calls here (solana-py's `logs_subscribe`)
   were verified against the installed library's actual signatures while
   building this, but the end-to-end connection has never been exercised
   live — this development environment cannot reach Solana RPC hosts at
   all. `hf-bot memecoin watch` exists specifically to verify this actually
   works, with your eyes on the output, before it is ever trusted inside the
   autonomous scalp loop.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from hf_trading_bot import solana_wallet

PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_MAX_RECENT = 100
_RECONNECT_BACKOFF_S = (2, 5, 10, 30, 60)


def ws_url(env: Optional[dict] = None) -> str:
    """SOLANA_WS_URL if set; otherwise derived from SOLANA_RPC_URL by
    swapping the scheme (https -> wss, http -> ws) — works for most
    providers (Helius, QuickNode, the public RPC) without a second setting."""
    e = env if env is not None else os.environ
    explicit = (e.get("SOLANA_WS_URL") or "").strip()
    if explicit:
        return explicit
    rpc = solana_wallet.rpc_url(env)
    if rpc.startswith("https://"):
        return "wss://" + rpc[len("https://"):]
    if rpc.startswith("http://"):
        return "ws://" + rpc[len("http://"):]
    return rpc


def extract_new_mint(tx: dict) -> Optional[str]:
    """Pure: given a getTransaction (jsonParsed) result, return a mint
    address present in postTokenBalances but absent from preTokenBalances —
    a strong signal a token was just created in this transaction. None if no
    such mint is found (a pump.fun buy/sell touches only existing mints)."""
    meta = (tx or {}).get("meta") or {}
    pre_mints = {b.get("mint") for b in (meta.get("preTokenBalances") or []) if b.get("mint")}
    post_mints = {b.get("mint") for b in (meta.get("postTokenBalances") or []) if b.get("mint")}
    new_mints = post_mints - pre_mints
    return sorted(new_mints)[0] if new_mints else None


class LiveFeed:
    """Owns one persistent WebSocket subscription in a background thread with
    its own asyncio event loop. Purely observational — never touches a
    private key, never places a trade. `recent()`/`status()` are the
    non-blocking read side that other code (the autotrade cycle, the
    dashboard) polls; they never touch the network themselves."""

    def __init__(self, env: Optional[dict] = None):
        self._env = env
        self._recent: deque = deque(maxlen=_MAX_RECENT)
        self._lock = threading.Lock()
        self._status: dict = {"connected": False, "detections": 0, "last_event_at": None,
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

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            return list(self._recent)[-limit:][::-1]   # newest first

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _record(self, mint: str) -> None:
        coin = {"address": mint, "symbol": None, "name": None,
               "created_at_ms": int(time.time() * 1000), "market_cap_usd": None,
               "sol_raised": None, "migrated": False, "source": "pumpfun_live"}
        with self._lock:
            self._recent.append(coin)
            self._status["detections"] += 1
            self._status["last_event_at"] = datetime.now(timezone.utc).isoformat()

    def _run_forever(self) -> None:
        import asyncio
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
        from solana.rpc.websocket_api import connect
        from solders.pubkey import Pubkey
        from solders.rpc.config import RpcTransactionLogsFilterMentions

        url = ws_url(self._env)
        program = Pubkey.from_string(PUMPFUN_PROGRAM_ID)
        async with connect(url) as ws:
            await ws.logs_subscribe(RpcTransactionLogsFilterMentions(program),
                                    commitment="confirmed")
            await ws.recv()   # the subscription-confirmation message
            with self._lock:
                self._status["connected"] = True
                self._status["last_error"] = None
            async for messages in ws:
                if self._stop.is_set():
                    return
                for msg in messages:
                    try:
                        logs_response = msg.result.value
                    except AttributeError:
                        continue
                    if logs_response.err is not None:
                        continue   # a failed tx created nothing real
                    self._handle_signature(str(logs_response.signature))

    def _handle_signature(self, signature: str) -> None:
        try:
            tx = solana_wallet.get_transaction(signature, env=self._env)
        except solana_wallet.WalletError as e:
            with self._lock:
                self._status["last_error"] = f"getTransaction failed: {e}"
            return
        if not tx:
            return
        mint = extract_new_mint(tx)
        if mint:
            self._record(mint)
