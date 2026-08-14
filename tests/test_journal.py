"""Decision journal — validation gates and calibration scoring. No network."""
import sqlite3

import pytest

from hf_trading_bot.journal import Decision, Journal, JournalError


@pytest.fixture
def journal():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return Journal(conn)


def good_decision(**kw):
    base = dict(
        symbol="NVDA",
        decision="BUY",
        conviction="medium",
        thesis="Datacenter GPU demand is structurally underestimated and the current "
               "price implies growth decelerating faster than orders suggest.",
        falsification="Hyperscaler capex guidance falls two consecutive quarters, or "
                      "gross margin drops below 60%.",
    )
    base.update(kw)
    return Decision(**base)


def test_records_a_valid_decision(journal):
    did = journal.record(good_decision())
    assert did == 1
    assert len(journal.open_decisions()) == 1


def test_buy_without_falsification_is_rejected(journal):
    # This is the whole point of the journal — no exit plan, no entry.
    with pytest.raises(JournalError, match="Falsification criteria are required"):
        journal.record(good_decision(falsification=""))


def test_sell_also_requires_falsification(journal):
    with pytest.raises(JournalError, match="Falsification criteria are required"):
        journal.record(good_decision(decision="SELL", falsification="hmm"))


def test_pass_does_not_require_falsification(journal):
    # Passing on an idea needs a reason but no exit plan — there's no position.
    journal.record(good_decision(decision="PASS", falsification=""))
    assert len(journal.all_decisions()) == 1


def test_thin_thesis_is_rejected(journal):
    with pytest.raises(JournalError, match="too short to be a real thesis"):
        journal.record(good_decision(thesis="going up"))


def test_invalid_decision_and_conviction_rejected(journal):
    with pytest.raises(JournalError, match="decision must be one of"):
        journal.record(good_decision(decision="YOLO"))
    with pytest.raises(JournalError, match="conviction must be one of"):
        journal.record(good_decision(conviction="certain"))


def test_symbol_and_decision_normalised(journal):
    journal.record(good_decision(symbol="nvda", decision="buy"))
    row = journal.all_decisions()[0]
    assert row["symbol"] == "NVDA"
    assert row["decision"] == "BUY"


def test_has_open_thesis_tracks_lifecycle(journal):
    assert journal.has_open_thesis("NVDA") is False
    did = journal.record(good_decision())
    assert journal.has_open_thesis("NVDA") is True
    journal.review(did, "right", pnl_pct=12.0)
    assert journal.has_open_thesis("NVDA") is False


def test_review_rejects_bad_outcome(journal):
    did = journal.record(good_decision())
    with pytest.raises(JournalError, match="outcome must be"):
        journal.review(did, "kinda")


def test_scorecard_empty_before_any_review(journal):
    journal.record(good_decision())
    assert journal.scorecard() == {"reviewed": 0}


def test_scorecard_computes_accuracy_and_discipline(journal):
    a = journal.record(good_decision(symbol="AAA"))
    b = journal.record(good_decision(symbol="BBB"))
    c = journal.record(good_decision(symbol="CCC"))
    journal.review(a, "right", pnl_pct=20.0, followed_own_rules=True)
    journal.review(b, "wrong", pnl_pct=-10.0, followed_own_rules=True)
    journal.review(c, "wrong", pnl_pct=-30.0, followed_own_rules=False)

    s = journal.scorecard()
    assert s["reviewed"] == 3
    assert s["accuracy_pct"] == pytest.approx(33.33, abs=0.1)
    assert s["discipline_pct"] == pytest.approx(66.67, abs=0.1)
    assert s["avg_pnl_pct"] == pytest.approx(-6.67, abs=0.1)


def test_scorecard_breaks_down_by_conviction(journal):
    hi = journal.record(good_decision(symbol="AAA", conviction="high"))
    lo = journal.record(good_decision(symbol="BBB", conviction="low"))
    journal.review(hi, "right")
    journal.review(lo, "wrong")

    by = journal.scorecard()["by_conviction"]
    assert by["high"] == {"n": 1, "right": 1}
    assert by["low"] == {"n": 1, "right": 0}


def test_unresolved_excluded_from_scorecard(journal):
    a = journal.record(good_decision(symbol="AAA"))
    b = journal.record(good_decision(symbol="BBB"))
    journal.review(a, "right")
    journal.review(b, "unresolved")
    assert journal.scorecard()["reviewed"] == 1
