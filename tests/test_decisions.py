"""What the decision log must refuse, and why each refusal earns its place."""
from __future__ import annotations
import json
import pytest

from hf_trading_bot.decisions import (
    Decision, read_decisions, log_decision, score, summary, render,
    decisions_needed,
)


def _d(**kw):
    base = dict(at="2026-10-20T09:40:00-04:00", ticker="ASTS", verdict="NO",
                stage="evaluate", rationale="valuation embeds flawless execution",
                price=50.0, benchmark=600.0)
    base.update(kw)
    return Decision(**base)


# --- what a verdict must carry -----------------------------------------------


def test_a_verdict_without_a_reason_is_refused():
    """A row with no rationale cannot be reviewed later, and reviewing it later
    is the only reason the log exists."""
    with pytest.raises(ValueError, match="rationale is required"):
        _d(rationale="   ")


def test_an_unzoned_stamp_is_refused():
    """Same failure as signaltrack: offsets mix the day ET moves to -05:00, and
    a mis-ordered log mis-pairs every window it measures."""
    with pytest.raises(ValueError, match="carries no UTC offset"):
        _d(at="2026-10-20T09:40:00")


def test_a_no_cannot_carry_a_size():
    """Declining to buy something is not a position. A size on a NO would flow
    into exposure totals as though capital had been committed."""
    with pytest.raises(ValueError, match="a NO carries no size"):
        _d(verdict="NO", size_usd=100.0)


def test_executed_without_approval_is_refused():
    """The standing rule, enforced in the type rather than in a prompt: no order
    is placed without Waldo's explicit approval of that specific proposal."""
    with pytest.raises(ValueError, match="marked executed without approval"):
        _d(verdict="BUY", size_usd=100.0, approved=None, executed=True)
    with pytest.raises(ValueError, match="marked executed without approval"):
        _d(verdict="BUY", size_usd=100.0, approved=False, executed=True)
    _d(verdict="BUY", size_usd=100.0, approved=True, executed=True)   # must not raise


def test_a_zero_price_is_refused():
    """A verdict marked at zero would divide by zero in scoring, or worse,
    silently report an infinite return."""
    with pytest.raises(ValueError, match="price must be a positive number"):
        _d(price=0.0)


# --- what the log must refuse on read ----------------------------------------


def test_a_duplicate_verdict_is_refused(tmp_path):
    """One run logged twice would count one decision as two in every statistic."""
    p = tmp_path / "log.jsonl"
    log_decision(_d(), p)
    with pytest.raises(ValueError, match="already has a verdict"):
        log_decision(_d(), p)


def test_a_hand_edited_row_is_caught_on_read(tmp_path):
    """Validation on read, not only on write: a row edited into the file by hand
    must fail the next time anything reads it, not feed a statistic."""
    p = tmp_path / "log.jsonl"
    log_decision(_d(), p)
    bad = json.loads(p.read_text().splitlines()[0])
    bad["verdict"] = "MAYBE"
    p.write_text(json.dumps(bad) + "\n")
    with pytest.raises(ValueError, match="not one of"):
        read_decisions(p)


# --- scoring ------------------------------------------------------------------


def test_a_no_is_right_when_the_name_lags():
    """A NO is a position in cash against the name. It is vindicated by the name
    underperforming, not by the name falling -- in a rising market almost
    nothing falls, so a raw return would score every NO as wrong."""
    d = _d(verdict="NO", price=50.0, benchmark=600.0)
    # name +10%, SPY +20% -> the NO avoided 10pp of underperformance.
    rows = score([d], {"ASTS": 55.0, "SPY": 720.0})
    assert rows[0]["right"] is True
    assert rows[0]["value_added"] == pytest.approx(0.10, abs=1e-9)
    # name +30%, SPY +20% -> the NO cost 10pp. This is the case a buys-only log
    # would never record at all.
    rows = score([d], {"ASTS": 65.0, "SPY": 720.0})
    assert rows[0]["right"] is False
    assert rows[0]["value_added"] == pytest.approx(-0.10, abs=1e-9)


def test_a_buy_that_rose_less_than_spy_is_not_a_win():
    """The benchmark is the hurdle. A name up 5% while SPY was up 20% destroyed
    15pp, and a log that called it a win would be measuring the market."""
    d = _d(verdict="BUY", size_usd=100.0, price=50.0, benchmark=600.0)
    rows = score([d], {"ASTS": 52.5, "SPY": 720.0})
    assert rows[0]["name_return"] > 0 and rows[0]["right"] is False


def test_an_unpriceable_verdict_raises_rather_than_being_dropped():
    """Skipping what cannot be priced would reintroduce, inside the scorer, the
    exact survivorship bias this log exists to prevent -- and it would drop the
    verdicts most likely to have gone wrong."""
    with pytest.raises(ValueError, match="no current price for ASTS"):
        score([_d()], {"SPY": 700.0})


def test_scoring_without_the_benchmark_raises():
    with pytest.raises(ValueError, match="no SPY price"):
        score([_d()], {"ASTS": 55.0})


# --- the honesty machinery ----------------------------------------------------


def test_a_hit_rate_is_not_called_a_result_until_it_could_be_one():
    """The whole module's point. Three decisions can produce a 100% hit rate;
    saying so without saying 194 are needed would be the error this project has
    made and documented before."""
    ds = [_d(at=f"2026-10-2{i}T09:40:00-04:00", verdict="NO") for i in range(3)]
    s = summary(ds, {"ASTS": 50.0, "SPY": 660.0})
    assert s["hit_rate"] == 1.0
    assert s["is_a_result"] is False
    assert "LOG, NOT A RESULT" in render(ds, {"ASTS": 50.0, "SPY": 660.0})


def test_the_power_requirement_moves_the_right_way():
    """A smaller edge needs more data. If this ever inverted, the module would
    be reassuring exactly when it should be cautioning."""
    assert decisions_needed(0.50, 0.55) > decisions_needed(0.50, 0.60) \
        > decisions_needed(0.50, 0.70)
    assert decisions_needed() == 194


def test_an_impossible_power_question_is_refused():
    with pytest.raises(ValueError, match="infinite data"):
        decisions_needed(0.5, 0.5)


def test_the_stage_breakdown_survives_an_empty_log():
    """A pipeline reporting nothing on its first morning must still print a
    shape, not crash -- the empty case is the normal case early on."""
    s = summary([])
    assert s["n"] == 0 and s["is_a_result"] is False
    assert set(s["by_stage"]) == {"scan", "evaluate", "risk", "decide"}
