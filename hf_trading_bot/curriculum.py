"""Load the per-agent curriculum and decide what to study next.

The curriculum is data (knowledge/curriculum.yaml); this module is the thin
seam the CLI and dashboard use to read it. No network, no LLM — just what to
study and what's left, given what the study_log says is already absorbed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Topic:
    agent_key: str
    topic: str
    why: str
    seeds: list[str]

    @property
    def slug(self) -> str:
        return self.topic


def _curriculum_path() -> Path:
    # knowledge/ sits at the repo root, next to hf_trading_bot/
    return Path(__file__).resolve().parent.parent / "knowledge" / "curriculum.yaml"


def load_curriculum(path: Optional[Path] = None) -> dict[str, list[Topic]]:
    import yaml

    p = path or _curriculum_path()
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, list[Topic]] = {}
    for agent_key, topics in data.items():
        out[agent_key] = [
            Topic(agent_key=agent_key, topic=t["topic"], why=t.get("why", ""),
                  seeds=list(t.get("seeds", [])))
            for t in (topics or [])
        ]
    return out


def agent_keys(path: Optional[Path] = None) -> list[str]:
    return list(load_curriculum(path).keys())


def next_topic(agent_key: str, studied: set[str],
               path: Optional[Path] = None) -> Optional[Topic]:
    """The first curriculum topic for this agent not yet in `studied`."""
    for t in load_curriculum(path).get(agent_key, []):
        if t.topic not in studied:
            return t
    return None


def coverage(agent_key: str, studied: set[str],
             path: Optional[Path] = None) -> tuple[int, int]:
    """(absorbed, total) for an agent, counting only curriculum topics."""
    topics = load_curriculum(path).get(agent_key, [])
    total = len(topics)
    absorbed = sum(1 for t in topics if t.topic in studied)
    return absorbed, total


def study_brief(t: Topic) -> str:
    """The instruction an executor (a Claude session) runs to study a topic."""
    seeds = "; ".join(t.seeds) if t.seeds else "(research anchors: use your judgement)"
    return (
        f"Use the {t.agent_key} agent to STUDY the topic '{t.topic}'.\n"
        f"Why it matters to your role: {t.why}\n"
        f"Research anchors (starting points, not a reading list): {seeds}\n\n"
        f"Research the concepts, frameworks, and documented history using your "
        f"web/news tools — do NOT reproduce copyrighted text. Then write a "
        f"distilled note to knowledge/{t.agent_key}/{t.slug}.md covering: the key "
        f"principles, 2-3 concrete historical cases, WHAT THIS CHANGES about how "
        f"you operate, and your sources. Persist a one-paragraph lesson with "
        f"`hf-bot memory persist --kind lesson --title '...' --body '...'` and "
        f"mirror it to the Agently brain if reachable. Finally run:\n"
        f"  hf-bot study record --agent {t.agent_key} --topic {t.topic} "
        f"--slug {t.slug} --sources <n>\n"
        f"and commit knowledge/{t.agent_key}/{t.slug}.md."
    )
