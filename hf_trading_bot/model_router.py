"""Tiered model routing — send easy work to cheap models, hard work to Claude.

The committee is expensive because a single question fans out into many
tool-driven Claude calls. A lot of what the team does, though, is not a hard
judgement call: researching a curriculum topic, summarising numbers, a plain
"what is X" question. Those don't need Claude — a cheaper model answers them
fine, for free or near-free, and saves the Claude budget for the calls that
actually decide capital.

This module is the routing seam:

    tier          provider (default)         used for
    ----          ----------------           --------
    "cheap"       NVIDIA (nemotron)          research / study / summarise
    "mid"         Ollama Cloud (qwen)        screening / triage
    "top"         claude (the committee)     buy/sell, valuation, red-team

Only "cheap" and "mid" are handled here — they are OpenAI-compatible chat
APIs reached over plain HTTP. "top" means "hand it back to the existing
`claude` executor", so this module never touches trading tools or orders: a
cheap model can *write*, it cannot place an order or run committee tools.

No third-party SDK: providers are read from the environment (a git-ignored
.env) and called with urllib, so nothing here needs credentials at import
time and the whole module tests offline by monkeypatching `_chat`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal, Optional

Tier = Literal["cheap", "mid", "top"]


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible chat endpoint. `api_key` is optional so a local
    Ollama (no key) works too."""
    name: str
    base_url: str
    model: str
    api_key: Optional[str] = None

    @property
    def configured(self) -> bool:
        # A base_url and model are the minimum; a hosted provider also needs a
        # key, but a localhost Ollama does not, so we don't force one here.
        return bool(self.base_url and self.model)


def load_providers(env: Optional[dict] = None) -> dict[Tier, Provider]:
    """Read the cheap/mid providers from the environment. `top` is Claude and
    is served by the existing executor, so it is not represented here.

    Env (all optional — an absent tier just isn't available):
        NVIDIA_API_KEY / NVIDIA_BASE_URL / NVIDIA_MODEL   -> cheap
        OLLAMA_API_KEY / OLLAMA_BASE_URL / OLLAMA_MODEL   -> mid
    """
    e = env if env is not None else os.environ
    return {
        "cheap": Provider(
            name="nvidia",
            base_url=e.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            model=e.get("NVIDIA_MODEL", "nvidia/nemotron-4-340b-instruct"),
            api_key=e.get("NVIDIA_API_KEY") or None,
        ),
        "mid": Provider(
            name="ollama",
            base_url=e.get("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            model=e.get("OLLAMA_MODEL", "qwen2.5-coder"),
            api_key=e.get("OLLAMA_API_KEY") or None,
        ),
    }


def available(env: Optional[dict] = None) -> dict[Tier, bool]:
    """Which non-Claude tiers actually have credentials to run. A hosted
    provider (base_url is not localhost) needs a key to count as available."""
    out: dict[Tier, bool] = {}
    for tier, p in load_providers(env).items():
        hosted = not ("localhost" in p.base_url or "127.0.0.1" in p.base_url)
        out[tier] = p.configured and (bool(p.api_key) or not hosted)
    return out


# Words that mean "this is a real capital / committee decision" — anything
# matching these stays on Claude ("top"), never a cheap text model, because
# the answer may stage an order or must run committee tools. Deliberately
# broad: when in doubt, routing UP to Claude is the safe error.
_TOP_MARKERS = (
    "buy", "sell", "short", "should i", "worth buying", "worth it",
    "position size", "how much", "allocate", "allocation", "trade",
    "order", "invest", "valuation", "intrinsic value", "fair value",
    "red team", "red-team", "committee", "portfolio", "rebalance",
    "risk of", "stop loss", "options", "leverage", "margin",
    # Recommendation-shaped asks — "best/which stock", "pick a ...": these
    # name capital to deploy even when phrased as "what is...", so route up.
    "stock", "etf", "dividend", "recommend", "which", "best ", "pick",
)
# Words that mark a low-stakes informational ask — safe for a cheap model.
_CHEAP_MARKERS = (
    "what is", "what are", "define", "definition", "explain", "explanation",
    "summarize", "summarise", "summary", "tell me about", "overview",
    "history of", "how does", "difference between", "glossary", "meaning of",
)


def classify(kind: str, prompt: str) -> Tier:
    """Pick a tier for a unit of work.

    - Study/research is always "cheap": distilling a note doesn't need Claude.
    - A console message is read for intent. Anything that smells like a real
      decision routes "top" (Claude); a plainly informational question routes
      "cheap"; the safe default for the ambiguous middle is "top".
    """
    if kind in ("study", "study_cycle", "research", "summarize"):
        return "cheap"
    text = (prompt or "").lower()
    if any(m in text for m in _TOP_MARKERS):
        return "top"
    if any(m in text for m in _CHEAP_MARKERS):
        return "cheap"
    return "top"


class RouterError(RuntimeError):
    pass


def _chat(provider: Provider, system: str, user: str,
          *, temperature: float = 0.4, max_tokens: int = 1400,
          timeout: int = 120) -> str:
    """One OpenAI-compatible /chat/completions call. Isolated so tests can
    monkeypatch it and the rest of the module runs with no network."""
    if not provider.configured:
        raise RouterError(f"provider {provider.name!r} is not configured")
    url = provider.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:  # noqa: PERF203
        detail = ex.read().decode(errors="replace")[:300]
        raise RouterError(f"{provider.name} HTTP {ex.code}: {detail}") from ex
    except urllib.error.URLError as ex:
        raise RouterError(f"{provider.name} unreachable: {ex.reason}") from ex
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as ex:
        raise RouterError(f"{provider.name} returned an unexpected shape: "
                          f"{str(data)[:200]}") from ex


def complete(tier: Tier, system: str, user: str, *,
             env: Optional[dict] = None, **kw) -> str:
    """Run a prompt on the given non-Claude tier. Raises RouterError if that
    tier isn't configured. ('top' is Claude — callers handle that path.)"""
    if tier == "top":
        raise RouterError("tier 'top' is served by the claude executor, not the router")
    provider = load_providers(env)[tier]
    return _chat(provider, system, user, **kw)


def list_models(tier: Tier, env: Optional[dict] = None, timeout: int = 30) -> list[str]:
    """GET the provider's OpenAI-compatible /models list — the authoritative
    set of model ids this key can actually call. Used by `models list` so you
    never have to guess a NVIDIA_MODEL / OLLAMA_MODEL name."""
    if tier == "top":
        raise RouterError("tier 'top' is Claude, not the router")
    provider = load_providers(env)[tier]
    if not provider.base_url:
        raise RouterError(f"tier {tier!r} has no base_url")
    url = provider.base_url.rstrip("/") + "/models"
    headers = {}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode(errors="replace")[:300]
        raise RouterError(f"{provider.name} HTTP {ex.code}: {detail}") from ex
    except urllib.error.URLError as ex:
        raise RouterError(f"{provider.name} unreachable: {ex.reason}") from ex
    rows = data.get("data", data) if isinstance(data, dict) else data
    ids = []
    for r in rows or []:
        mid = r.get("id") if isinstance(r, dict) else str(r)
        if mid:
            ids.append(mid)
    return sorted(ids)


def ping(tier: Tier, env: Optional[dict] = None) -> str:
    """A tiny liveness check for `models check`: returns the model's reply to a
    one-word prompt, or raises RouterError."""
    return complete(tier, "You are a health check. Reply with one word.",
                    "Say READY.", env=env, max_tokens=8, temperature=0.0)
