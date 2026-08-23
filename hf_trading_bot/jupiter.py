"""Jupiter aggregator client — quotes and swap transactions for any SPL token,
including pump.fun tokens once they have on-chain liquidity.

Two calls, in order: `quote()` asks "what would I get", `swap_transaction()`
turns that quote into an unsigned transaction for solana_wallet to sign. This
module never touches a private key and never submits anything — it only
builds what a wallet later signs.

Base URL history, since this has already bitten a live run once: the old
`quote-api.jup.ag/v6` endpoint was deprecated (Jupiter's own DevRel
announced it alongside Price V2 and Token V1) and its DNS records were
fully removed — live testing hit this as a persistent "Jupiter unreachable:
getaddrinfo failed" that no retry could fix, because there was nothing
listening at that hostname anymore, not a transient network blip. The
current endpoint is `api.jup.ag/swap/v1` — same query params, request
body, and response field names (outAmount, priceImpactPct,
swapTransaction) as the old v6 API, so only the base URL changed here.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

SOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_BASE_URL = "https://api.jup.ag/swap/v1"
_TIMEOUT = 20
_HEADERS = {
    # A default urllib User-Agent (or none at all) can trip Cloudflare-style
    # bot detection on public APIs — a browser-shaped UA avoids that class of
    # false block without changing anything about the request itself.
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


class JupiterError(RuntimeError):
    pass


def base_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("JUPITER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def quote(input_mint: str, output_mint: str, amount: int, *,
         slippage_bps: int = 100, env: Optional[dict] = None) -> dict:
    """A swap quote. `amount` is in the input token's smallest unit (lamports
    for SOL). Raises JupiterError if there's no route (e.g. the token has no
    liquidity yet — common for a pump.fun token pre-migration)."""
    params = (f"inputMint={input_mint}&outputMint={output_mint}&amount={amount}"
             f"&slippageBps={slippage_bps}")
    url = f"{base_url(env)}/quote?{params}"
    req = urllib.request.Request(url, method="GET", headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode(errors="replace")[:300]
        raise JupiterError(f"Jupiter quote HTTP {ex.code}: {detail}") from ex
    except urllib.error.URLError as ex:
        raise JupiterError(f"Jupiter unreachable: {ex.reason}") from ex
    if not data or "outAmount" not in data:
        raise JupiterError(f"no route for {input_mint} -> {output_mint} "
                           f"(no liquidity, or the token/pair is wrong): {data!r}")
    return data


def swap_transaction(quote_response: dict, user_pubkey: str, *,
                     env: Optional[dict] = None) -> str:
    """Build the unsigned, base64-encoded swap transaction for `quote_response`.
    Nothing is signed or sent here."""
    body = json.dumps({
        "quoteResponse": quote_response,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    }).encode()
    url = f"{base_url(env)}/swap"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=dict(_HEADERS, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode(errors="replace")[:300]
        raise JupiterError(f"Jupiter swap HTTP {ex.code}: {detail}") from ex
    except urllib.error.URLError as ex:
        raise JupiterError(f"Jupiter unreachable: {ex.reason}") from ex
    tx = data.get("swapTransaction")
    if not tx:
        raise JupiterError(f"no swapTransaction in response: {str(data)[:300]}")
    return tx


def price_impact_pct(quote_response: dict) -> float:
    try:
        return float(quote_response.get("priceImpactPct") or 0.0) * 100.0
    except (TypeError, ValueError):
        return 0.0
