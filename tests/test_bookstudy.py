"""Regression tests for the friction-aware book study.

These exist because the study's first version annualised every rebalance
frequency as if it were monthly, and reported a 263% CAGR for an annual
rebalance. A wrong number that looks plausible is worse than no number.
"""
import math
from hf_trading_bot.bookstudy import Result, stats


def _flat(n, step):
    """A curve that doubles exactly once over n periods of `step` days."""
    start = 1289.51
    r = (2.0) ** (1.0 / n)
    return Result(equity=[start * r ** (k + 1) for k in range(n)],
                  dates=[""] * n, turnover=0.0, cost_paid=0.0,
                  tax_paid=0.0, n_rebal=n)


def test_cagr_is_annualised_from_the_actual_period_not_an_assumed_month():
    # 15 annual periods that double total -> CAGR must be 2^(1/15)-1 = 4.7%,
    # NOT the 2^(12/15)-1 = 74% a month-assuming annualiser would report.
    s = stats(_flat(15, 252), step=252)
    assert math.isclose(s["cagr"], 2 ** (1 / 15) - 1, rel_tol=1e-6)
    assert math.isclose(s["years"], 15.0, rel_tol=1e-6)


def test_monthly_and_annual_agree_on_cagr_for_the_same_horizon():
    # Same 15-year doubling, sampled monthly vs annually: CAGR must match.
    a = stats(_flat(180, 21), step=21)
    b = stats(_flat(15, 252), step=252)
    assert math.isclose(a["cagr"], b["cagr"], rel_tol=1e-3)


def test_constant_return_curve_reports_zero_sharpe_not_float_noise():
    # pstdev of nominally equal floats is ~1e-17, not 0. Without a guard that
    # divides into an astronomical Sharpe.
    assert stats(_flat(180, 21), step=21)["sharpe"] == 0.0


def test_sharpe_scales_as_sqrt_of_periods_per_year():
    # Same per-period return series sampled as monthly vs quarterly must give
    # Sharpes differing by exactly sqrt(12/4).
    start = 1289.51
    seq = [0.03, -0.01, 0.05, -0.02] * 20
    eq, v = [], start
    for x in seq:
        v *= 1 + x
        eq.append(v)
    r = Result(equity=eq, dates=[""] * len(eq), turnover=0.0,
               cost_paid=0.0, tax_paid=0.0, n_rebal=len(eq))
    mo = stats(r, step=21)["sharpe"]
    qt = stats(r, step=63)["sharpe"]
    assert math.isclose(mo / qt, math.sqrt(3.0), rel_tol=1e-9)


def test_stats_reports_n_so_a_thin_sample_cannot_hide():
    assert stats(_flat(15, 252), step=252)["n"] == 15
