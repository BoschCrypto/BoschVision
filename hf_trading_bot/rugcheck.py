"""RugCheck.xyz — a free, keyless (for reads) REST API giving a composite
risk score plus holder-concentration and LP-lock data for Solana tokens.

Why this file exists: memecoin.pumpfun_risk_flags() only checks mint/freeze
authority — the one universal on-chain check available for a brand-new
pump.fun coin. It has no visibility into the single most-cited red flag from
real pump.fun scalpers: a bundled/insider launch, where one wallet (or a
handful funded from the same source in the same block) holds an outsized
share of supply while looking, on-chain, like normal early trading. RugCheck
computes exactly that — top-holder concentration and LP-lock status — from
a service built for this.

BE HONEST ABOUT THE LIMITS:
1. This is RugCheck's own official API (not scraped), but it is a small
   team with a documented history of rate-limit/downtime incidents. Treat a
   failed request or a missing report as "no additional signal" — NEVER as
   "this token is safe." A coin seconds old is often not indexed yet; that
   is normal, not an error.
2. Field names below are matched defensively (multiple candidate keys)
   because the exact response shape is not perfectly documented/stable.
   An unexpected shape degrades to "field unavailable," never a crash.
3. This is a supplementary signal, called by memecoin.py only right before
   a buy actually executes (not for every scanned candidate) — that keeps
   real-world usage well under the free-tier rate limit and matches what
   it's for: a final check before money moves, not a screening filter run
   dozens of times a cycle.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_BASE_URL = "https://api.rugcheck.xyz/v1"
_TIMEOUT_S = 8
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


class RugCheckError(RuntimeError):
    """A real network/parse failure talking to RugCheck. Callers should treat
    this the same as 'no data available' — RugCheck being down must never
    block a trade or be mistaken for a safety signal either way."""


def base_url(env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    return (e.get("RUGCHECK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _api_key(env: Optional[dict] = None) -> Optional[str]:
    e = env if env is not None else os.environ
    return (e.get("RUGCHECK_API_KEY") or "").strip() or None


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def get_report(token_address: str, *, env: Optional[dict] = None) -> Optional[dict]:
    """Fetch and normalize a token's RugCheck report. Returns None (not an
    error) when the token simply isn't indexed yet — common for a coin only
    seconds old. Raises RugCheckError only for an actual network/parse
    failure, so callers can tell "no data yet" apart from "unreachable"."""
    url = f"{base_url(env)}/tokens/{token_address}/report"
    headers = dict(_HEADERS)
    key = _api_key(env)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None
        raise RugCheckError(f"RugCheck HTTP {ex.code}") from ex
    except urllib.error.URLError as ex:
        raise RugCheckError(f"RugCheck unreachable: {ex.reason}") from ex
    except (json.JSONDecodeError, ValueError) as ex:
        raise RugCheckError(f"RugCheck returned unparseable data: {ex}") from ex
    if not isinstance(raw, dict):
        return None
    return _normalize(raw)


def _normalize(raw: dict) -> dict:
    score = _first(raw, "score_normalised", "score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None

    top_holder_pct = _extract_top_holder_pct(_first(raw, "topHolders", "top_holders", default=[]))

    lp_locked_pct = _first(raw, "lpLockedPct", "lp_locked_pct")
    if lp_locked_pct is None:
        for m in (_first(raw, "markets", default=[]) or []):
            if isinstance(m, dict) and isinstance(m.get("lp"), dict):
                lp_locked_pct = _first(m["lp"], "lpLockedPct", "lockedPct")
                if lp_locked_pct is not None:
                    break
    try:
        lp_locked_pct = float(lp_locked_pct) if lp_locked_pct is not None else None
    except (TypeError, ValueError):
        lp_locked_pct = None

    risks = [{"name": r.get("name"), "level": r.get("level"), "description": r.get("description")}
            for r in (_first(raw, "risks", default=[]) or []) if isinstance(r, dict)]

    return {"score": score, "top_holder_pct": top_holder_pct,
           "lp_locked_pct": lp_locked_pct, "risks": risks}


def _extract_top_holder_pct(holders: Any) -> Optional[float]:
    if not isinstance(holders, list) or not holders:
        return None
    # Prefer non-LP/pool holders — a real wallet's outsized share is the
    # bundled/insider signal; the pool itself legitimately holds a lot.
    non_lp_pcts, all_pcts = [], []
    for h in holders:
        if not isinstance(h, dict):
            continue
        pct = h.get("pct")
        if pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        all_pcts.append(pct)
        if not h.get("isLp") and not h.get("is_lp"):
            non_lp_pcts.append(pct)
    pool = non_lp_pcts or all_pcts
    return max(pool) if pool else None
