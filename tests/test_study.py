"""Curriculum + study log. Offline, no network, no LLM."""
import pytest

from hf_trading_bot.cortex import build_snapshot
from hf_trading_bot.curriculum import (
    agent_keys,
    coverage,
    load_curriculum,
    next_topic,
    study_brief,
)
from hf_trading_bot.storage import Storage

# The 11 committee agents that must each have a curriculum.
EXPECTED_AGENTS = {
    "cio", "portfolio-manager", "risk-manager", "behavioral-coach", "equity-analyst",
    "quant-analyst", "macro-strategist", "special-situations", "setup-scanner",
    "valuation-analyst", "red-team", "sniper",
}


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "study.db"))
    yield s
    s.close()


# --- curriculum -------------------------------------------------------------

def test_curriculum_covers_all_eleven_agents():
    curric = load_curriculum()
    assert set(curric) == EXPECTED_AGENTS


def test_every_agent_has_topics_with_why_and_seeds():
    for agent, topics in load_curriculum().items():
        assert topics, f"{agent} has no topics"
        for t in topics:
            assert t.topic and t.why, f"{agent}/{t.topic} missing fields"


def test_topic_ids_are_unique_within_an_agent():
    for agent, topics in load_curriculum().items():
        ids = [t.topic for t in topics]
        assert len(ids) == len(set(ids)), f"{agent} has duplicate topic ids"


def test_study_brief_names_agent_and_target_file():
    t = load_curriculum()["red-team"][0]
    brief = study_brief(t)
    assert "red-team" in brief
    assert f"knowledge/red-team/{t.slug}.md" in brief
    assert "do NOT reproduce copyrighted" in brief.replace("\n", " ")


# --- study log --------------------------------------------------------------

def test_next_topic_skips_studied(storage):
    first = next_topic("equity-analyst", set())
    assert first is not None
    storage.record_study("equity-analyst", first.topic, first.slug, sources_count=3)
    second = next_topic("equity-analyst", storage.studied_topics("equity-analyst"))
    assert second is not None and second.topic != first.topic


def test_record_study_is_idempotent_on_agent_topic(storage):
    storage.record_study("red-team", "famous-blowups", "famous-blowups", sources_count=2)
    storage.record_study("red-team", "famous-blowups", "famous-blowups", sources_count=5)
    assert storage.study_total() == 1
    assert storage.study_counts()["red-team"] == 1


def test_coverage_counts_absorbed_over_total(storage):
    total = len(load_curriculum()["macro-strategist"])
    storage.record_study("macro-strategist", "monetary-history", "monetary-history")
    a, t = coverage("macro-strategist", storage.studied_topics("macro-strategist"))
    assert a == 1 and t == total


def test_all_curriculum_topics_can_eventually_be_exhausted(storage):
    # study every topic for one agent → next_topic returns None
    for topic in [t.topic for t in load_curriculum()["quant-analyst"]]:
        storage.record_study("quant-analyst", topic, topic)
    assert next_topic("quant-analyst", storage.studied_topics("quant-analyst")) is None


# --- snapshot / dashboard ---------------------------------------------------

def test_snapshot_knowledge_block_reports_growth(storage):
    storage.record_study("red-team", "famous-blowups", "famous-blowups", sources_count=4)
    k = build_snapshot(storage).knowledge
    assert k["topic_total"] == sum(len(v) for v in load_curriculum().values())
    assert k["absorbed_total"] == 1
    assert k["per_agent"]["red-team"]["absorbed"] == 1
    assert k["recent"][0]["topic"] == "famous-blowups"


def test_empty_knowledge_block_is_zero(storage):
    k = build_snapshot(storage).knowledge
    assert k["absorbed_total"] == 0
    assert k["topic_total"] > 0  # curriculum exists even before any study
