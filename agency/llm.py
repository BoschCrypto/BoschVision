"""Model access for the agents: one `generate()` call, three backends.

    backend      reaches Claude through                  needs
    -------      ----------------------                  -----
    anthropic    the official `anthropic` Python SDK     ANTHROPIC_API_KEY (or an
                                                         `ant auth login` profile)
    claude-cli   `claude -p` (Claude Code, headless)     Claude Code installed + logged in
    offline      nothing: each agent's built-in template nothing

`auto` (the default) picks anthropic when an API key is set, else claude-cli
when `claude` is on PATH, else offline. The agency therefore always runs, and
its drafts get real the moment a model is connected.

Every call is entered in the ledger *before* it is made, failures included,
and refused once AGENCY_MAX_LLM_CALLS_PER_DAY is reached. Agents that run
around the clock must not be able to run up a bill through a retry loop.

Agents get structured JSON back (`OUTPUT_SCHEMA`): a one-line summary for the
HQ feed, an optional lead score, and a list of work items.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Optional

from .config import Settings
from .store import Store, start_of_day

KINDS = ("blog_post", "social_post", "newsletter", "ad_campaign", "email_draft",
         "proposal", "blueprint", "report", "recommendation", "alert")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "score": {"type": "integer"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["kind", "title", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "score", "items"],
    "additionalProperties": False,
}

# $ per million tokens (input, output) — only for the spend estimate in HQ.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
# Models that accept the server-side refusal fallback (`fallbacks: "default"`):
# if a safety classifier declines a request (SENTRY's security wording can trip
# one), the API re-runs it on Anthropic's recommended fallback model.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


def resolve_backend(choice: str, env: Optional[dict] = None) -> str:
    e = env if env is not None else os.environ
    if choice in ("anthropic", "claude-cli", "offline"):
        return choice
    if (e.get("ANTHROPIC_API_KEY") or e.get("ANTHROPIC_AUTH_TOKEN")) and importlib.util.find_spec("anthropic"):
        return "anthropic"
    if shutil.which("claude"):
        return "claude-cli"
    return "offline"


def parse_json_lenient(text: str) -> dict:
    """The JSON object in a model reply, tolerating code fences or stray prose
    (the CLI backend can't always enforce the schema)."""
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("the model did not return JSON") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as ex:
            raise LLMError(f"the model returned malformed JSON: {ex}") from None
    if not isinstance(data, dict):
        raise LLMError("the model returned JSON that is not an object")
    return data


def normalize(data: dict) -> dict:
    """Coerce a reply into {summary, score, items[]} with known kinds and no
    empty items, whatever the backend produced."""
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        body = str(it.get("body") or "").strip()
        if not (title and body):
            continue
        kind = it.get("kind") if it.get("kind") in KINDS else "report"
        items.append({"kind": kind, "title": title[:200], "body": body})
    try:
        score = max(0, min(10, int(data.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    return {"summary": str(data.get("summary") or "").strip()[:400], "score": score, "items": items}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    price = PRICES.get(model)
    if not price:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


class LLM:
    def __init__(self, settings: Settings, store: Store, env: Optional[dict] = None):
        self.settings = settings
        self.store = store
        self.backend = resolve_backend(settings.llm_backend, env)
        self.model = settings.model
        self.cap = settings.max_llm_calls_per_day
        self._lock = threading.Lock()
        self._client = None
        self._claude_bin = shutil.which("claude")

    @property
    def connected(self) -> bool:
        return self.backend != "offline"

    def usage_today(self) -> dict:
        return self.store.llm_usage_since(start_of_day())

    def generate(self, agent: str, system: str, user: str,
                 fallback: Callable[[], dict]) -> dict:
        """Run one agent prompt. Offline, the agent's own template answers."""
        if not self.connected:
            return normalize(fallback())
        with self._lock:
            used = self.usage_today()["calls"]
            if used >= self.cap:
                raise BudgetExceeded(f"daily model budget reached ({used}/{self.cap} calls); "
                                     "resumes after midnight")
            call_id = self.store.open_llm_call(agent, self.backend, self.model)
        try:
            if self.backend == "anthropic":
                data, tokens_in, tokens_out, cost = self._call_anthropic(system, user)
            else:
                data, tokens_in, tokens_out, cost = self._call_cli(system, user)
        except LLMError as ex:
            self.store.close_llm_call(call_id, ok=False, error=str(ex)[:300])
            raise
        except Exception as ex:  # an unexpected SDK/CLI failure still closes the ledger row
            self.store.close_llm_call(call_id, ok=False, error=f"{type(ex).__name__}: {ex}"[:300])
            raise LLMError(f"{type(ex).__name__}: {ex}") from ex
        if cost is None:
            cost = estimate_cost(self.model, tokens_in, tokens_out)
        self.store.close_llm_call(call_id, ok=True, input_tokens=tokens_in,
                                  output_tokens=tokens_out, cost_usd=cost)
        return normalize(data)

    # -- backends ----------------------------------------------------------
    def _output_config(self) -> dict:
        cfg: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        if self.settings.effort:
            cfg["effort"] = self.settings.effort
        return cfg

    def _call_anthropic(self, system: str, user: str):
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        kwargs = dict(model=self.model, max_tokens=16000, system=system,
                      messages=[{"role": "user", "content": user}],
                      output_config=self._output_config())
        try:
            if self.model in _FALLBACK_MODELS:
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as ex:
            raise LLMError("the Claude API rejected the credentials (check ANTHROPIC_API_KEY)") from ex
        except anthropic.RateLimitError as ex:
            raise LLMError("rate-limited by the Claude API; the agent retries next cycle") from ex
        except anthropic.APIStatusError as ex:
            raise LLMError(f"Claude API error {ex.status_code}: {ex.message}") from ex
        except anthropic.APIConnectionError as ex:
            raise LLMError("could not reach the Claude API") from ex
        if resp.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        if resp.stop_reason == "max_tokens":
            raise LLMError("the reply was cut off at max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        usage = resp.usage
        return parse_json_lenient(text), usage.input_tokens or 0, usage.output_tokens or 0, None

    def _call_cli(self, system: str, user: str):
        if not self._claude_bin:
            raise LLMError("the `claude` CLI is not on PATH")
        # No tools: an agent drafting copy has no business editing files or
        # running commands. The prompt goes on stdin (no argv length limit),
        # and the CLI runs outside the repo so no project hooks fire.
        cmd = [self._claude_bin, "-p", "--model", self.model, "--tools", "",
               "--system-prompt", system, "--output-format", "json",
               "--json-schema", json.dumps(OUTPUT_SCHEMA), "--no-session-persistence"]
        try:
            p = subprocess.run(cmd, input=user, capture_output=True, text=True,
                               timeout=900, cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired as ex:
            raise LLMError("`claude -p` timed out after 15 minutes") from ex
        if p.returncode != 0:
            raise LLMError(f"`claude -p` exited {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
        outer = parse_json_lenient(p.stdout)
        if outer.get("is_error"):
            raise LLMError(f"`claude -p` reported an error: {str(outer.get('result'))[:300]}")
        structured = outer.get("structured_output")
        data = structured if isinstance(structured, dict) else (
            outer if "items" in outer else parse_json_lenient(str(outer.get("result") or "")))
        usage = outer.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        tokens_out = int(usage.get("output_tokens") or 0)
        cost = outer.get("total_cost_usd")
        return data, tokens_in, tokens_out, (float(cost) if cost is not None else None)
