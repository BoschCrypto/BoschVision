"""SENTINEL — news and event scanning. The slow lane.

Runs on its own thread, polls at its own cadence, and publishes a single
decaying scalar to the blackboard. The tick loop reads that scalar in
nanoseconds and never waits on it. If the news source hangs, times out, or
returns garbage, the worst case is that SENTINEL's signal ages out and the
system trades on price alone — which is exactly the correct degradation.

Three scoring tiers, cheapest first:

  1. Lexicon      a weighted keyword model over the headline. Microseconds,
                  deterministic, works offline, and genuinely good on the
                  unambiguous cases ("halted", "guides below", "FDA
                  approves") which are the ones that move price hardest.
  2. Model        for headlines the lexicon finds ambiguous, escalate to a
                  cheap LLM through the existing hf_trading_bot model
                  router. Optional — absent credentials, tier 1 stands.
  3. Decay        every score carries a half-life. A headline from 20
                  minutes ago is not 80% as important as a fresh one; it
                  is mostly priced in. TTL enforces that it eventually
                  reads as exactly zero rather than a faint permanent tilt.

Honest limitation, stated because it affects how you should use this: by
the time a headline reaches a public feed, algorithmic readers on paid
low-latency wires have had it for anywhere from 50 ms to several seconds
and the first move is over. SENTINEL is therefore built as a CONTEXT and
VETO layer — it tells the other agents what regime they are in and when to
stand down — not as a source of entry timing. Treating a free news feed as
an entry trigger is a reliable way to buy the top of the spike.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from ..bus import Blackboard
from ..types import NS_PER_S, Signal
from .base import SlowAgent

# Weighted lexicon. Values are directional impact, not sentiment: "probe"
# is negative for the stock regardless of how neutrally it is written.
_BULL = {
    "beats": 0.6, "beat": 0.6, "tops": 0.5, "raises guidance": 0.9,
    "raises outlook": 0.85, "upgrade": 0.6, "upgraded": 0.6, "approval": 0.8,
    "approves": 0.75, "wins": 0.5, "awarded": 0.5, "record revenue": 0.7,
    "buyback": 0.55, "acquire": 0.5, "acquisition": 0.45, "takeover": 0.8,
    "partnership": 0.35, "breakthrough": 0.6, "surges": 0.5, "soars": 0.6,
    "ahead of estimates": 0.65, "reinstates dividend": 0.5,
}
_BEAR = {
    "misses": -0.65, "miss": -0.6, "cuts guidance": -0.95, "lowers outlook": -0.9,
    "downgrade": -0.6, "downgraded": -0.6, "probe": -0.7, "investigation": -0.7,
    "subpoena": -0.75, "lawsuit": -0.45, "recall": -0.7, "halted": -0.9,
    "halt": -0.85, "bankruptcy": -1.0, "chapter 11": -1.0, "fraud": -0.95,
    "resigns": -0.5, "steps down": -0.45, "layoffs": -0.3, "warns": -0.7,
    "plunges": -0.6, "slumps": -0.5, "delays": -0.5, "withdraws": -0.6,
    "short report": -0.85, "restatement": -0.9, "going concern": -1.0,
    "dilution": -0.55, "offering": -0.45, "secondary offering": -0.6,
}
# Headlines that mean "do not trade this name right now" regardless of sign.
_HALT_WORDS = ("halted", "trading halt", "circuit breaker", "sec investigation",
               "chapter 11", "going concern", "restatement")

_WORD_RE = re.compile(r"[a-z0-9']+")


def score_headline(text: str) -> tuple[float, bool, str]:
    """Return (impact in [-1,1], is_halt_class, matched terms).

    Pure function, no I/O, microseconds. Tested directly.
    """
    if not text:
        return 0.0, False, ""
    low = text.lower()
    halt = any(w in low for w in _HALT_WORDS)
    score = 0.0
    hits: list[str] = []
    # Multi-word phrases first so "raises guidance" does not also count as
    # a bare "raises"-style partial through a later single-word rule.
    for table in (_BULL, _BEAR):
        for phrase, w in table.items():
            if " " in phrase and phrase in low:
                score += w
                hits.append(phrase)
    words = set(_WORD_RE.findall(low))
    for table in (_BULL, _BEAR):
        for phrase, w in table.items():
            if " " not in phrase and phrase in words:
                score += w
                hits.append(phrase)
    # Negation: "not expected to miss" should not read as a miss. Crude but
    # catches the common construction; anything subtler escalates to tier 2.
    if " not " in f" {low} " or "no longer" in low:
        score *= -0.3
    if score > 1.0:
        score = 1.0
    elif score < -1.0:
        score = -1.0
    return score, halt, ",".join(hits[:4])


class Sentinel(SlowAgent):
    name = "SENTINEL"

    def __init__(
        self,
        symbol: str,
        interval_s: float = 20.0,
        ttl_s: float = 600.0,
        half_life_s: float = 180.0,
        feed_url: Optional[str] = None,
        use_model: bool = False,
        timeout_s: float = 4.0,
    ) -> None:
        self.symbol = symbol
        self.interval_s = interval_s
        self.ttl_ns = int(ttl_s * NS_PER_S)
        self.half_life_s = half_life_s
        self.feed_url = feed_url
        self.use_model = use_model
        self.timeout_s = timeout_s
        self._seen: set[str] = set()
        self._events: list[tuple[float, float, str]] = []   # (epoch_s, impact, headline)
        self.last_error: str = ""
        self.poll_count = 0

    # -- feed -------------------------------------------------------------

    def fetch(self) -> list[str]:
        """Pull headlines. Override, or pass feed_url for a JSON endpoint
        returning {"headlines": [...]} or a bare list of strings.

        Every failure mode returns [] and records the error. A news agent
        that raises takes the trading system down with it."""
        if not self.feed_url:
            return []
        try:
            req = urllib.request.Request(
                self.feed_url,
                headers={"User-Agent": "velocity-sentinel/0.1"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                raw = r.read(1_000_000)
            data = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return []
        if isinstance(data, dict):
            data = data.get("headlines") or data.get("items") or []
        out = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                h = item.get("headline") or item.get("title") or item.get("summary")
                if h:
                    out.append(str(h))
        return out[:50]

    # -- scoring ----------------------------------------------------------

    def _escalate(self, headline: str) -> Optional[float]:
        """Tier 2: ask a cheap model. Returns None if unavailable."""
        if not self.use_model:
            return None
        try:
            from hf_trading_bot.model_router import load_providers, route  # type: ignore
        except Exception:
            return None
        try:
            providers = load_providers()
            p = providers.get("cheap") or providers.get("mid")
            if not p or not p.configured:
                return None
            prompt = (
                "You score financial headlines for immediate price impact on the "
                f"named stock ({self.symbol}). Reply with ONLY a number from -1.0 "
                "(maximally bearish) to 1.0 (maximally bullish). No words.\n\n"
                f"Headline: {headline}"
            )
            txt = route("cheap", prompt, providers=providers)  # type: ignore[call-arg]
            m = re.search(r"-?\d*\.?\d+", str(txt))
            if not m:
                return None
            v = float(m.group())
            return max(-1.0, min(1.0, v))
        except Exception as e:  # never let the slow lane kill the session
            self.last_error = f"model: {type(e).__name__}"
            return None

    def ingest(self, headlines: list[str], now_s: Optional[float] = None) -> int:
        """Score new headlines into the event list. Returns how many were new."""
        now_s = now_s if now_s is not None else time.time()
        new = 0
        for h in headlines:
            key = h.strip().lower()[:160]
            if not key or key in self._seen:
                continue
            self._seen.add(key)
            new += 1
            impact, halt, _hits = score_headline(h)
            if abs(impact) < 0.25 and not halt:
                esc = self._escalate(h)
                if esc is not None:
                    impact = esc
            if halt:
                # Encoded as a maximal-magnitude event so the arbiter's
                # halt check trips; direction is kept for the record.
                impact = -1.0 if impact <= 0 else 1.0
            self._events.append((now_s, impact, h[:200]))
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])
        if len(self._events) > 200:
            del self._events[:100]
        return new

    def aggregate(self, now_s: Optional[float] = None) -> tuple[float, float, str]:
        """Collapse live events into (bias, confidence, reason) with decay."""
        now_s = now_s if now_s is not None else time.time()
        hl = self.half_life_s
        total = 0.0
        weight = 0.0
        newest = ""
        newest_age = 1e18
        for ts, impact, head in self._events:
            age = now_s - ts
            if age > hl * 6:
                continue
            decay = 0.5 ** (age / hl)
            total += impact * decay
            weight += decay
            if age < newest_age:
                newest_age = age
                newest = head
        if weight <= 0:
            return 0.0, 0.0, "no live news"
        bias = total / weight
        bias = max(-1.0, min(1.0, bias))
        # Confidence tracks live corroboration directly: it must fall to
        # ~0 as the last event decays, so a single stale headline cannot
        # keep tilting the book. A flat floor (0.35 + ...) looked fine but
        # left a 10-minute-old item voting at 0.36 forever.
        conf = 0.85 * weight / (0.6 + weight)
        if abs(bias) < 0.15:
            conf *= 0.4
        return bias, conf, f"{newest[:70]} (t-{newest_age:.0f}s)"

    def poll(self, bb: Blackboard) -> Optional[Signal]:
        self.poll_count += 1
        heads = self.fetch()
        self.ingest(heads)
        bias, conf, reason = self.aggregate()
        return Signal(self.name, bb.last_ts_ns or time.time_ns(), bias, conf,
                      self.ttl_ns, reason)
