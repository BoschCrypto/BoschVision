"""Tiered model-router and cheap-model study path. No network — the single
HTTP seam (`model_router._chat`) and `model_router.complete` are monkeypatched.
"""
from pathlib import Path

import pytest

from hf_trading_bot import model_router, study_runner
from hf_trading_bot.storage import Storage


# --- provider loading / availability ---------------------------------------

def test_load_providers_reads_env():
    env = {"NVIDIA_API_KEY": "nv", "NVIDIA_MODEL": "nemo",
           "OLLAMA_API_KEY": "ol", "OLLAMA_MODEL": "qwen"}
    p = model_router.load_providers(env)
    assert p["cheap"].name == "nvidia" and p["cheap"].model == "nemo"
    assert p["mid"].name == "ollama" and p["mid"].api_key == "ol"


def test_available_needs_key_for_hosted_but_not_local():
    hosted = {"NVIDIA_MODEL": "nemo"}  # hosted default URL, no key
    assert model_router.available(hosted)["cheap"] is False
    keyed = {"NVIDIA_API_KEY": "nv", "NVIDIA_MODEL": "nemo"}
    assert model_router.available(keyed)["cheap"] is True
    local = {"OLLAMA_BASE_URL": "http://localhost:11434/v1", "OLLAMA_MODEL": "q"}
    assert model_router.available(local)["mid"] is True  # local needs no key


# --- classification ---------------------------------------------------------

@pytest.mark.parametrize("kind,text,expected", [
    ("study", "anything", "cheap"),
    ("study_cycle", "anything", "cheap"),
    ("console", "what is a moving average", "cheap"),
    ("console", "explain the yield curve", "cheap"),
    ("console", "should I buy NVDA", "top"),
    ("console", "what is the best dividend stock to own", "top"),  # 'buy'-class intent
    ("console", "size a position in AAPL", "top"),
    ("console", "give me a market overview", "cheap"),
    ("console", "run the committee on TSLA", "top"),
    ("console", "hmm", "top"),  # ambiguous -> safe default is Claude
])
def test_classify(kind, text, expected):
    assert model_router.classify(kind, text) == expected


def test_complete_top_is_rejected():
    with pytest.raises(model_router.RouterError):
        model_router.complete("top", "s", "u")


def test_complete_calls_chat(monkeypatch):
    seen = {}

    def fake_chat(provider, system, user, **kw):
        seen.update(provider=provider.name, system=system, user=user)
        return "hello"

    monkeypatch.setattr(model_router, "_chat", fake_chat)
    out = model_router.complete("cheap", "sys", "usr",
                                env={"NVIDIA_API_KEY": "k", "NVIDIA_MODEL": "m"})
    assert out == "hello"
    assert seen["provider"] == "nvidia" and seen["user"] == "usr"


# --- cheap-model study actually persists ------------------------------------

@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "cortex.db"))
    yield s
    s.close()


def test_study_one_writes_note_and_records(tmp_path, storage, monkeypatch):
    monkeypatch.setattr(study_runner, "_knowledge_root", lambda: tmp_path / "knowledge")
    monkeypatch.setattr(model_router, "complete",
                        lambda *a, **k: "## Key principles\nBuy low.\n")

    agent = "equity-analyst"
    assert storage.studied_topics(agent) == set()
    result = study_runner.study_one(agent, storage,
                                    env={"NVIDIA_API_KEY": "k", "NVIDIA_MODEL": "m"})

    assert result["studied"] is True
    note = tmp_path / "knowledge" / agent / f"{result['slug']}.md"
    assert note.exists()
    text = note.read_text()
    assert "Buy low." in text
    assert "not committee-reviewed" in text          # provenance header present
    assert result["topic"] in storage.studied_topics(agent)  # library ticked up


def test_study_one_advances_to_next_topic(tmp_path, storage, monkeypatch):
    monkeypatch.setattr(study_runner, "_knowledge_root", lambda: tmp_path / "knowledge")
    monkeypatch.setattr(model_router, "complete", lambda *a, **k: "note body")
    agent = "equity-analyst"
    first = study_runner.study_one(agent, storage, env={"NVIDIA_API_KEY": "k"})
    second = study_runner.study_one(agent, storage, env={"NVIDIA_API_KEY": "k"})
    assert first["topic"] != second["topic"]  # doesn't restudy the same topic


def test_study_one_empty_reply_raises(tmp_path, storage, monkeypatch):
    monkeypatch.setattr(study_runner, "_knowledge_root", lambda: tmp_path / "knowledge")
    monkeypatch.setattr(model_router, "complete", lambda *a, **k: "   ")
    with pytest.raises(model_router.RouterError):
        study_runner.study_one("equity-analyst", storage, env={"NVIDIA_API_KEY": "k"})


def test_study_one_curriculum_complete(tmp_path, storage, monkeypatch):
    monkeypatch.setattr(study_runner, "_knowledge_root", lambda: tmp_path / "knowledge")
    monkeypatch.setattr(model_router, "complete", lambda *a, **k: "body")
    # Mark every topic for one agent as already studied.
    from hf_trading_bot.curriculum import load_curriculum
    agent = "equity-analyst"
    for t in load_curriculum().get(agent, []):
        storage.record_study(agent, t.topic, t.slug)
    result = study_runner.study_one(agent, storage, env={"NVIDIA_API_KEY": "k"})
    assert result["studied"] is False and "complete" in result["reason"]
