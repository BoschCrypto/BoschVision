"""Contribution log and benchmark counterfactual. Pure math, no network."""
import sqlite3

import pytest

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.portfolio import (
    Comparison,
    Contribution,
    ContributionLog,
    PortfolioError,
    counterfactual,
)


def bar(t: str, c: float) -> Bar:
    return Bar(t=t, o=c, h=c, l=c, c=c, v=1_000_000)


# A tidy price series: SPY at 100 then 200 — an exact doubling, so the
# arithmetic below can be checked by hand.
BARS = [
    bar("2026-01-02", 100.0),
    bar("2026-02-02", 125.0),
    bar("2026-03-02", 200.0),
]


@pytest.fixture
def log():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return ContributionLog(conn)


# --- counterfactual math ----------------------------------------------------

def test_single_contribution_doubles_with_the_index():
    # $1,000 at 100 buys 10 shares; 10 shares at 200 = $2,000.
    c = counterfactual([Contribution("2026-01-02", 1000.0)], BARS, actual_value=1500.0)
    assert c.benchmark_shares == pytest.approx(10.0)
    assert c.benchmark_value == pytest.approx(2000.0)
    assert c.total_contributed == pytest.approx(1000.0)
    assert c.benchmark_return_pct == pytest.approx(100.0)


def test_multiple_contributions_accumulate_shares():
    # 1000/100 = 10 shares, then 1000/125 = 8 shares → 18 shares.
    c = counterfactual(
        [Contribution("2026-01-02", 1000.0), Contribution("2026-02-02", 1000.0)],
        BARS,
        actual_value=3000.0,
    )
    assert c.benchmark_shares == pytest.approx(18.0)
    assert c.benchmark_value == pytest.approx(3600.0)
    assert c.total_contributed == pytest.approx(2000.0)


def test_gap_is_actual_minus_benchmark_in_dollars():
    # Behind the index: 18 shares → $3,600, but the account holds $3,000.
    c = counterfactual(
        [Contribution("2026-01-02", 1000.0), Contribution("2026-02-02", 1000.0)],
        BARS,
        actual_value=3000.0,
    )
    assert c.gap == pytest.approx(-600.0)
    assert c.actual_return_pct == pytest.approx(50.0)
    assert c.benchmark_return_pct == pytest.approx(80.0)
    assert c.excess_pct == pytest.approx(-30.0)


def test_beating_the_index_gives_a_positive_gap():
    c = counterfactual([Contribution("2026-01-02", 1000.0)], BARS, actual_value=2500.0)
    assert c.gap == pytest.approx(500.0)
    assert c.excess_pct == pytest.approx(50.0)


def test_weekend_contribution_buys_at_next_trading_close():
    # 2026-01-01 has no bar; the next available close is 100 on 01-02.
    c = counterfactual([Contribution("2026-01-01", 1000.0)], BARS, actual_value=0.0)
    assert c.benchmark_shares == pytest.approx(10.0)


def test_withdrawal_sells_shares_at_that_close():
    # +1000 at 100 (10 shares), then -500 at 125 (-4 shares) → 6 shares.
    c = counterfactual(
        [Contribution("2026-01-02", 1000.0), Contribution("2026-02-02", -500.0)],
        BARS,
        actual_value=1200.0,
    )
    assert c.benchmark_shares == pytest.approx(6.0)
    assert c.benchmark_value == pytest.approx(1200.0)


def test_marks_to_the_most_recent_close():
    c = counterfactual([Contribution("2026-01-02", 1000.0)], BARS, actual_value=0.0)
    assert c.as_of == "2026-03-02"


def test_contribution_after_last_bar_is_an_error():
    with pytest.raises(PortfolioError, match="later than the last available bar"):
        counterfactual([Contribution("2027-01-01", 1000.0)], BARS, actual_value=0.0)


def test_empty_price_history_is_an_error():
    with pytest.raises(PortfolioError, match="no price history"):
        counterfactual([Contribution("2026-01-02", 1000.0)], [], actual_value=0.0)


def test_no_contributions_is_an_error():
    with pytest.raises(PortfolioError, match="no contributions recorded"):
        counterfactual([], BARS, actual_value=0.0)


def test_zero_contributed_does_not_divide_by_zero():
    c = Comparison(
        benchmark="SPY", total_contributed=0.0, actual_value=0.0,
        benchmark_value=0.0, benchmark_shares=0.0, as_of="2026-03-02",
    )
    assert c.actual_return_pct == 0.0
    assert c.benchmark_return_pct == 0.0


# --- contribution log -------------------------------------------------------

def test_round_trip_through_the_log(log):
    log.add(Contribution("2026-02-02", 500.0, note="Feb paycheck"))
    log.add(Contribution("2026-01-02", 1000.0))
    got = log.all()
    assert [c.contributed_on for c in got] == ["2026-01-02", "2026-02-02"]  # sorted
    assert got[1].note == "Feb paycheck"


def test_log_rejects_a_malformed_date(log):
    with pytest.raises(PortfolioError, match="YYYY-MM-DD"):
        log.add(Contribution("02/02/2026", 500.0))


def test_log_rejects_a_zero_amount(log):
    with pytest.raises(PortfolioError, match="non-zero"):
        log.add(Contribution("2026-02-02", 0.0))


def test_first_date_is_none_when_empty(log):
    assert log.first_date() is None


def test_first_date_tracks_the_earliest_contribution(log):
    log.add(Contribution("2026-02-02", 500.0))
    log.add(Contribution("2026-01-02", 500.0))
    assert log.first_date() == "2026-01-02"


def test_log_feeds_the_counterfactual_directly(log):
    log.add(Contribution("2026-01-02", 1000.0))
    log.add(Contribution("2026-02-02", 1000.0))
    c = counterfactual(log.all(), BARS, actual_value=3000.0)
    assert c.benchmark_shares == pytest.approx(18.0)
    assert c.gap == pytest.approx(-600.0)


# --- CLI wiring -------------------------------------------------------------

class StubProvider:
    def daily_bars_range(self, symbol, start=None, end=None):
        return BARS


@pytest.fixture
def cli_config(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(f"db_path: {tmp_path / 'test.db'}\nbroker: paper\n")
    return str(cfg)


def run_cli(cli_config, *args):
    from unittest.mock import patch

    from click.testing import CliRunner

    from hf_trading_bot.cli import cli

    with patch("hf_trading_bot.cli._build_provider", return_value=StubProvider()):
        return CliRunner().invoke(cli, ["--config", cli_config, *args])


def test_cli_contribute_then_compare(cli_config):
    assert run_cli(cli_config, "portfolio", "contribute",
                   "--amount", "1000", "--date", "2026-01-02").exit_code == 0
    r = run_cli(cli_config, "portfolio", "compare", "--value", "1500")
    assert r.exit_code == 0
    assert "2,000.00" in r.output      # 10 shares at 200
    assert "-500.00" in r.output       # behind the index by $500
    assert "Behind SPY" in r.output


def test_cli_compare_ahead_of_index(cli_config):
    run_cli(cli_config, "portfolio", "contribute", "--amount", "1000", "--date", "2026-01-02")
    r = run_cli(cli_config, "portfolio", "compare", "--value", "2500")
    assert r.exit_code == 0
    assert "Ahead of SPY" in r.output


def test_cli_compare_without_contributions_fails_clearly(cli_config):
    r = run_cli(cli_config, "portfolio", "compare", "--value", "1000")
    assert r.exit_code != 0
    assert "No contributions recorded" in r.output


def test_cli_rejects_bad_date(cli_config):
    r = run_cli(cli_config, "portfolio", "contribute", "--amount", "100", "--date", "02/02/2026")
    assert r.exit_code != 0
    assert "YYYY-MM-DD" in r.output


def test_cli_list_shows_total(cli_config):
    run_cli(cli_config, "portfolio", "contribute", "--amount", "1000", "--date", "2026-01-02")
    run_cli(cli_config, "portfolio", "contribute", "--amount", "-250", "--date", "2026-02-02")
    r = run_cli(cli_config, "portfolio", "list")
    assert r.exit_code == 0
    assert "750.00" in r.output and "TOTAL" in r.output
