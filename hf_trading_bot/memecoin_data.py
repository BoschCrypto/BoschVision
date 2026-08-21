"""Solana token discovery via DexScreener's public API (no key required).

DexScreener indexes pairs once they have on-chain liquidity, so a pump.fun
token appears here after it migrates to a Raydium pool (or trades directly on
its bonding curve pair, where DexScreener also tracks it) — not necessarily
the instant it's created. That lag is a feature for this bot: liquidity is
exactly what makes a token sellable later, and "no data yet" is itself a
signal to stay away.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

BASE_URL = "https://api.dexscreener.com"
_TIMEOUT = 20


class DexScreenerError(RuntimeError):
    pass


def _get(path: str, env: Optional[dict] = None) -> Any:
    e = env if env is not None else os.environ
    base = (e.get("DEXSCREENER_BASE_URL") or BASE_URL).rstrip("/")
    req = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        raise DexScreenerError(
            f"DexScreener HTTP {ex.code}: {ex.read().decode(errors='replace')[:300]}") from ex
    except urllib.error.URLError as ex:
        raise DexScreenerError(f"DexScreener unreachable: {ex.reason}") from ex


def _normalize(pair: dict) -> dict:
    base = pair.get("baseToken") or {}
    liq = pair.get("liquidity") or {}
    vol = pair.get("volume") or {}
    return {
        "address": base.get("address"),
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "price_usd": float(pair["priceUsd"]) if pair.get("priceUsd") else None,
        "liquidity_usd": float(liq.get("usd") or 0),
        "volume_24h_usd": float(vol.get("h24") or 0),
        "fdv_usd": float(pair["fdv"]) if pair.get("fdv") else None,
        "market_cap_usd": float(pair["marketCap"]) if pair.get("marketCap") else None,
        "pair_created_at": pair.get("pairCreatedAt"),
        "dex_id": pair.get("dexId"),
        "chain_id": pair.get("chainId"),
        "url": pair.get("url"),
    }


def search(query: str, *, env: Optional[dict] = None) -> list[dict]:
    """Search DexScreener for `query` (symbol, name, or mint address),
    restricted to Solana pairs, highest liquidity first."""
    data = _get(f"/latest/dex/search?q={urllib.parse.quote(query)}", env=env)
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return [_normalize(p) for p in pairs]


def get_token(address: str, *, env: Optional[dict] = None) -> Optional[dict]:
    """The best (highest-liquidity) known pair for one Solana token mint."""
    data = _get(f"/tokens/v1/solana/{address}", env=env)
    pairs = data if isinstance(data, list) else (data or {}).get("pairs") or []
    pairs = [p for p in pairs if p.get("chainId") == "solana"]
    if not pairs:
        return None
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return _normalize(pairs[0])


def trending(limit: int = 20, *, env: Optional[dict] = None) -> list[dict]:
    """Currently-boosted (paid promotion, not necessarily quality) Solana
    tokens, enriched with price/liquidity/volume. This is momentum/attention,
    not a recommendation — most boosted tokens are exactly the high-risk kind
    this whole feature exists to warn about."""
    boosts = _get("/token-boosts/top/v1", env=env)
    addrs = [b.get("tokenAddress") for b in (boosts or [])
            if b.get("chainId") == "solana" and b.get("tokenAddress")]
    seen: list[str] = []
    for a in addrs:
        if a not in seen:
            seen.append(a)
    out: list[dict] = []
    for a in seen[: max(0, limit)]:
        t = get_token(a, env=env)
        if t:
            out.append(t)
    return out
