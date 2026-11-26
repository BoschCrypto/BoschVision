"""Cross-sectional factor computations for the Gated Momentum strategy.

Every function here takes a bar series plus an ``asof`` index and uses **only**
``bars[:asof + 1]`` — the bar at ``asof`` is the last one the caller is allowed
to have seen. Nothing reads forward. That constraint is the whole point of this
module: look-ahead bias in a cross-sectional backtest is silent, it inflates
results in the direction you were hoping for, and it is almost impossible to
spot once the numbers are in a table.

The strategy these feed is specified in the "Gated Momentum" note: quality-gated
12-1 momentum, confirmed by 52-week-high proximity, gated on a market regime
filter, sized by inverse volatility, rebalanced monthly.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from hf_trading_bot.data.bars import Bar

# Trading-day conventions. A year is ~252 sessions; a month ~21.
TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21
TRADING_DAYS_52W = 252


def momentum_12_1(bars: Sequence[Bar], asof: int) -> Optional[float]:
    """Trailing 12-month return skipping the most recent month.

    Measured close-to-close from ``asof - 252`` to ``asof - 21``. The skipped
    month is not a tuning choice — short-horizon reversal runs against the
    12-month signal, so including the most recent month mixes two effects that
    point in opposite directions. Jegadeesh & Titman (1993) and essentially
    every replication since skip it.

    Returns None when there is not enough history, rather than a partial
    window: a 6-month "12-month" momentum score is a different signal wearing
    the same name.
    """
    if asof < TRADING_DAYS_YEAR:
        return None
    start = bars[asof - TRADING_DAYS_YEAR]
    end = bars[asof - TRADING_DAYS_MONTH]
    if start.c <= 0:
        return None
    return end.c / start.c - 1


def pct_of_52w_high(bars: Sequence[Bar], asof: int) -> Optional[float]:
    """Latest close as a fraction of the highest close in the trailing 52 weeks.

    George & Hwang (2004) found nearness to the 52-week high subsumes much of
    past returns' predictive power, and — unlike raw momentum — those forecast
    returns do not reverse in the long run. Used here as confirmation: a name
    can post a strong 12-1 score and still be well off its high after a failed
    spike, and the momentum score alone cannot tell "climbing" from "fell back
    from higher".

    Uses closes rather than intraday highs deliberately. An intraday spike that
    never held is not a level the stock actually reached in any meaningful
    sense, and including it makes the ratio jumpier without making it more
    informative.
    """
    if asof < TRADING_DAYS_52W:
        return None
    window = bars[asof - TRADING_DAYS_52W: asof + 1]
    high = max(b.c for b in window)
    if high <= 0:
        return None
    return bars[asof].c / high


def atr(bars: Sequence[Bar], asof: int, period: int = 20) -> Optional[float]:
    """Average true range over `period` sessions, in price units.

    True range accounts for gaps (it takes the previous close into the range),
    which matters for position sizing: a stock that gaps 5% overnight is riskier
    than its intraday ranges alone suggest.
    """
    if asof < period:
        return None
    total = 0.0
    for i in range(asof - period + 1, asof + 1):
        prev_close = bars[i - 1].c
        b = bars[i]
        total += max(b.h - b.l, abs(b.h - prev_close), abs(b.l - prev_close))
    return total / period


def atr_pct(bars: Sequence[Bar], asof: int, period: int = 20) -> Optional[float]:
    """ATR as a fraction of price — the unit that is comparable across names.

    Sizing on raw ATR would make a $400 stock look riskier than a $20 one purely
    because of its price level, which is a units error, not a risk measurement.
    """
    a = atr(bars, asof, period)
    if a is None:
        return None
    price = bars[asof].c
    if price <= 0:
        return None
    return a / price


def avg_dollar_volume(bars: Sequence[Bar], asof: int, period: int = 20) -> Optional[float]:
    """Mean close x volume over `period` sessions — the liquidity screen.

    This is the filter that keeps the strategy honest about reachability.
    Momentum is strongest on paper in microcaps, which is exactly where it is
    untradeable; screening it out sacrifices measured alpha to buy
    executability.
    """
    if asof < period - 1:
        return None
    window = bars[asof - period + 1: asof + 1]
    return sum(b.c * b.v for b in window) / period


def month_end_indices(bars: Sequence[Bar]) -> list[int]:
    """Indices of the last trading day of each calendar month in `bars`.

    Rebalance decisions are made from data through a month end and executed on
    the next session, so these are the *signal* dates, not the trade dates.
    """
    out: list[int] = []
    for i in range(len(bars) - 1):
        if bars[i].t[:7] != bars[i + 1].t[:7]:
            out.append(i)
    return out


def monthly_closes(bars: Sequence[Bar], asof: int) -> list[float]:
    """Month-end closes at or before `asof`, oldest first."""
    return [bars[i].c for i in month_end_indices(bars[: asof + 1])]


def regime_risk_on(
    spy_bars: Sequence[Bar],
    asof: int,
    sma_months: int = 10,
    trend_months: int = 24,
) -> Optional[bool]:
    """The gate the strategy is named for. True = deploy, False = go to cash.

    Two conditions, both required:
      1. SPY's latest month-end close is above its `sma_months`-month SMA;
      2. SPY's trailing `trend_months` return is positive.

    Daniel & Moskowitz (2016) located momentum crashes in market-wide "panic
    states" — high volatility following declines, and contemporaneous with
    rebounds — rather than in individual names going wrong. This is the cheap
    retail-computable proxy for that state. It is deliberately a market-level
    switch rather than a per-position stop: momentum returns come from a small
    number of large winners, and stops cut precisely those.

    Returns None when history is too short to evaluate, which callers must
    treat as "do not deploy" rather than as risk-on.
    """
    closes = monthly_closes(spy_bars, asof)
    if len(closes) < max(sma_months, trend_months) + 1:
        return None
    sma = sum(closes[-sma_months:]) / sma_months
    above_trend = closes[-1] > sma
    trailing = closes[-1] / closes[-(trend_months + 1)] - 1
    return bool(above_trend and trailing > 0)


def inverse_vol_weights(
    vols: dict[str, float],
    max_weight: float = 0.15,
) -> dict[str, float]:
    """Weights proportional to 1/volatility, normalised, then capped.

    Equal *dollar* weighting silently makes the most volatile name the largest
    contributor to portfolio risk. Inverse-vol equalises what each position can
    actually do to the book.

    The cap is applied iteratively: capping one name frees weight that must be
    redistributed, which can push another name over the cap in turn. A single
    pass would leave the constraint violated, so this repeats until it holds
    (or until every name is at the cap, when the cap is looser than 1/n and no
    redistribution is possible).
    """
    usable = {s: v for s, v in vols.items() if v and v > 0}
    if not usable:
        return {}

    raw = {s: 1.0 / v for s, v in usable.items()}
    total = sum(raw.values())
    weights = {s: r / total for s, r in raw.items()}

    # If the cap is below equal-weight it cannot be satisfied; fall back to
    # equal weight rather than silently returning something that breaks it.
    if max_weight <= 1.0 / len(weights):
        return {s: 1.0 / len(weights) for s in weights}

    for _ in range(len(weights)):
        over = {s: w for s, w in weights.items() if w > max_weight + 1e-12}
        if not over:
            break
        free = sum(weights[s] - max_weight for s in over)
        under = {s: w for s, w in weights.items() if s not in over}
        under_total = sum(under.values())
        for s in over:
            weights[s] = max_weight
        if under_total <= 0:
            break
        for s in under:
            weights[s] += free * (under[s] / under_total)
    return weights


def annualised_sharpe(daily_returns: Sequence[float], rf_annual: float = 0.0) -> Optional[float]:
    """Sharpe from a daily return series, annualised by sqrt(252).

    `rf_annual` is subtracted as a daily equivalent. Left at zero by default and
    stated in the report rather than assumed away — in a period with a 5% cash
    rate, a Sharpe computed against zero flatters the strategy.
    """
    if len(daily_returns) < 2:
        return None
    rf_daily = rf_annual / TRADING_DAYS_YEAR
    excess = [r - rf_daily for r in daily_returns]
    mean = sum(excess) / len(excess)
    var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return (mean / sd) * math.sqrt(TRADING_DAYS_YEAR)


def max_drawdown(equity: Sequence[float]) -> Optional[float]:
    """Deepest peak-to-trough decline in an equity curve, as a negative fraction."""
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst
