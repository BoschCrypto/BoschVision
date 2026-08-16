"""Run a study on a cheap model and actually persist the note.

The claude study path works because Claude has tools: it researches, writes
`knowledge/<agent>/<slug>.md`, and calls `study record`. A plain chat model
(nemotron, qwen) has no tools — it can only return text. So the *harness*
does the tool work here: it picks the next topic, asks the cheap model to
write just the note body, then writes the file and records the topic itself.

That closes the gap that made local/cheap models useless for study: with this
path a non-tool model genuinely grows the library (the Knowledge panel's
X/51 ticks up), no Claude tokens spent.

Note quality is lower than Claude's and, unlike the claude path, there is no
live web research or `memory persist` — it's a distillation from the model's
own knowledge against the curriculum brief. Notes carry a header saying so, so
a cheap-written note is never mistaken for a full committee-grade one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hf_trading_bot import model_router
from hf_trading_bot.cortex import CODENAMES
from hf_trading_bot.curriculum import Topic, next_topic, study_brief


def _knowledge_root() -> Path:
    return Path(__file__).resolve().parent.parent / "knowledge"


def _note_system(agent_key: str) -> str:
    codename = CODENAMES.get(agent_key, agent_key.upper())
    return (
        f"You are {codename}, the investment committee's {agent_key} specialist, "
        "writing a durable study note for your own future reference. Write in "
        "clear Markdown. Be concrete and disciplined. Do NOT reproduce "
        "copyrighted text — distil principles in your own words. Do not ask "
        "questions; produce the note."
    )


def _note_user(t: Topic) -> str:
    return (
        f"{study_brief(t)}\n\n"
        "Return ONLY the note body in Markdown with these sections:\n"
        "## Key principles\n## Concrete cases\n## What this changes about how I "
        "operate\n## Open questions\n\n"
        "Keep it tight and useful — no preamble, no sign-off."
    )


def study_one(agent_key: str, storage, *, tier: model_router.Tier = "cheap",
              env: Optional[dict] = None) -> dict:
    """Study the agent's next uncovered topic on a cheap model and persist it.

    Returns a dict describing what happened. Raises model_router.RouterError if
    the tier isn't configured or the call fails (caller decides whether to fall
    back to Claude). If the agent has nothing left to study, returns
    {"studied": False, "reason": "curriculum complete"}.
    """
    studied = storage.studied_topics(agent_key)
    t = next_topic(agent_key, studied)
    if t is None:
        return {"studied": False, "agent": agent_key, "reason": "curriculum complete"}

    body = model_router.complete(tier, _note_system(agent_key), _note_user(t), env=env)
    if not body.strip():
        raise model_router.RouterError("cheap model returned an empty note")

    provider = model_router.load_providers(env)[tier]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"# {t.topic}\n\n"
        f"> Studied by {CODENAMES.get(agent_key, agent_key.upper())} "
        f"({agent_key}) on {stamp} via `{provider.name}:{provider.model}` "
        f"(tier: {tier}). Distilled from model knowledge against the curriculum "
        f"brief — not live-researched, not committee-reviewed.\n\n"
    )
    path = _knowledge_root() / agent_key / f"{t.slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body.strip() + "\n", encoding="utf-8")

    storage.record_study(agent_key, t.topic, t.slug, sources_count=0)
    return {
        "studied": True, "agent": agent_key, "topic": t.topic, "slug": t.slug,
        "path": str(path.relative_to(_knowledge_root().parent)),
        "provider": f"{provider.name}:{provider.model}", "tier": tier,
        "chars": len(body),
    }
