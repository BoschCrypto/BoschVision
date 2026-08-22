"""Brand-new pump.fun coin discovery — direct from pump.fun's own (unofficial,
undocumented) frontend API, not DexScreener.

Why this file exists and DexScreener isn't enough: DexScreener only indexes a
pair once it has an on-chain liquidity pool, which for a pump.fun token means
after migration to Raydium (or once DexScreener happens to pick up the
bonding-curve pair) — there is real lag. A token that is 30 seconds old, the
kind Photon's "Memescope" shows, usually is not there yet. This module talks
to the same (unofficial) API pump.fun's own website calls, so "new" actually
means new.

BE HONEST ABOUT THE FRAGILITY: this is not a documented, stable API. Pump.fun
has changed this endpoint's host and shape before and can again without
notice, the same way DexScreener's Cloudflare rules blocked the default
User-Agent until that was fixed. If this starts erroring, the fix is almost
always: check pump.fun's current frontend for its new API host (open the
site, look at the network tab) and update PUMPFUN_BASE_URL in .env. Test with
`hf-bot memecoin newcoins --raw` before ever trusting this in the autonomous
scalp loop.

The bigger, honest limitation: a coin from this feed usually has NO
comparable "liquidity" figure yet (a bonding curve is not a liquidity pool
the way Raydium's is), so the liquidity-based rug checks in memecoin.py
cannot meaningfully apply here. The only universal structural safety check
left is mint/freeze authority — everything else is inherently higher risk.
That is not a bug to fix; it is what trading a token this early means.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

DEFAULT_BASE_URL = "https://frontend-api-v3.pump.fun"
_TIMEOUT = 20
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


class PumpFunError(RuntimeError):
    pass


def base_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("PUMPFUN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _get(path: str, *, env: Optional[dict] = None) -> Any:
    url = f"{base_url(env)}{path}"
    req = urllib.request.Request(url, method="GET", headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        raise PumpFunError(
            f"pump.fun HTTP {ex.code}: {ex.read().decode(errors='replace')[:300]} — "
            f"this is an unofficial API and may have changed; see pumpfun_data.py") from ex
    except urllib.error.URLError as ex:
        raise PumpFunError(f"pump.fun unreachable: {ex.reason}") from ex
    except (json.JSONDecodeError, ValueError) as ex:
        raise PumpFunError(f"pump.fun returned unparseable data: {ex}") from ex


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _normalize(raw: dict) -> Optional[dict]:
    """pump.fun's frontend API field names have shifted before — this reads
    defensively across the names that have been observed, rather than
    assuming one fixed shape. Returns None for a row too malformed to use."""
    address = _first(raw, "mint", "address", "tokenAddress")
    if not address:
        return None
    created_raw = _first(raw, "created_timestamp", "createdTimestamp", "created_at")
    created_at_ms = None
    if created_raw is not None:
        try:
            created_at_ms = int(created_raw)
            if created_at_ms < 10_000_000_000:   # looks like seconds, not ms
                created_at_ms *= 1000
        except (TypeError, ValueError):
            created_at_ms = None
    market_cap = _first(raw, "usd_market_cap", "market_cap", "marketCapUsd")
    sol_raised = _first(raw, "real_sol_reserves", "sol_raised", "virtual_sol_reserves")
    complete = bool(_first(raw, "complete", "migrated", default=False))
    return {
        "address": address,
        "symbol": _first(raw, "symbol", "ticker"),
        "name": _first(raw, "name"),
        "created_at_ms": created_at_ms,
        "market_cap_usd": float(market_cap) if market_cap is not None else None,
        "sol_raised": float(sol_raised) / 1_000_000_000.0 if sol_raised and
                      float(sol_raised) > 1000 else (float(sol_raised) if sol_raised else None),
        "migrated": complete,
        "source": "pumpfun",
    }


def list_new_coins(limit: int = 30, *, include_migrated: bool = False,
                   env: Optional[dict] = None) -> list[dict]:
    """The most recently created pump.fun coins, newest first. Raises
    PumpFunError on any network/parsing failure — callers decide whether to
    treat 'this unofficial API broke' as fatal or skip a cycle."""
    params = urllib.parse.urlencode({
        "offset": 0, "limit": limit, "sort": "created_timestamp",
        "order": "DESC", "includeNsfw": "false",
    })
    data = _get(f"/coins?{params}", env=env)
    rows = data if isinstance(data, list) else (data or {}).get("coins") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        n = _normalize(r)
        if n is None:
            continue
        if n["migrated"] and not include_migrated:
            continue
        out.append(n)
    return out
