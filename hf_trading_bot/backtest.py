"""Historical backtest replay. Long-only, one position at a time, $10k
notional per trade, entered and exited at the close of the signal bar.

Walks the bar series day by day so every strategy sees only past data at
each decision point (an expanding window) — no lookahead."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.strategies.registry import evaluate

NOTIONAL = 10_000.0
MIN_BARS = 5


@dataclass
class ReplayTrade:
    entry_date: str
    entry_price: float
    exit_date: Optional[str]
    exit_price: Optional[float]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    holding_days: Optional[int]
    open_at_end: bool


def _day(b: Bar) -> str:
    return b.t[:10]


def _build(bars: list[Bar], entry_idx: int, exit_idx: Optional[int]) -> ReplayTrade:
    entry = bars[entry_idx]
    if exit_idx is None:
        return ReplayTrade(_day(entry), entry.c, None, None, None, None, None, True)
    exit_ = bars[exit_idx]
    qty = NOTIONAL / entry.c
    return ReplayTrade(
        entry_date=_day(entry),
        entry_price=entry.c,
        exit_date=_day(exit_),
        exit_price=exit_.c,
        pnl_usd=qty * (exit_.c - entry.c),
        pnl_pct=(exit_.c / entry.c - 1) * 100,
        holding_days=exit_idx - entry_idx,
        open_at_end=False,
    )


def replay(bars: list[Bar], strategy_key: str, params: Optional[dict] = None) -> list[ReplayTrade]:
    trades: list[ReplayTrade] = []
    open_idx: Optional[int] = None
    for i in range(MIN_BARS, len(bars)):
        result = evaluate(strategy_key, bars[: i + 1], params)
        if result.signal == "entry" and open_idx is None:
            open_idx = i
        elif result.signal == "exit" and open_idx is not None:
            trades.append(_build(bars, open_idx, i))
            open_idx = None
    if open_idx is not None:
        trades.append(_build(bars, open_idx, None))
    return trades


@dataclass
class ReplayStats:
    total_trades: int
    wins: int
    losses: int
    win_rate: Optional[float]
    cagr: Optional[float]
    sharpe: Optional[float]
    max_drawdown: Optional[float]
    trades_per_year: Optional[float]
    avg_win_pct: Optional[float]
    avg_loss_pct: Optional[float]
    # Fraction of the backtest window actually spent holding a position. A
    # strategy invested 20% of the time took far less risk than buy-and-hold,
    # so comparing raw CAGR without this is misleading in the strategy's
    # favour.
    time_in_market_pct: Optional[float] = None
    total_return_pct: Optional[float] = None


@dataclass
class BenchmarkStats:
    """Buy-and-hold of a single symbol over the same window — the hurdle any
    active strategy has to clear to have been worth running."""

    symbol: str
    total_return_pct: float
    cagr: float
    max_drawdown: float
    bars: int


def buy_and_hold(bars: list[Bar], symbol: str, years: float) -> Optional[BenchmarkStats]:
    """Buy at the first close, hold to the last. Includes the full drawdown
    the holder would actually have had to sit through."""
    if len(bars) < 2 or years <= 0:
        return None
    first, last = bars[0].c, bars[-1].c
    if first <= 0:
        return None

    peak = bars[0].c
    max_dd = 0.0
    for b in bars:
        peak = max(peak, b.c)
        max_dd = min(max_dd, b.c / peak - 1)

    total_return = (last / first - 1) * 100
    cagr = ((last / first) ** (1 / years) - 1) * 100
    return BenchmarkStats(
        symbol=symbol,
        total_return_pct=total_return,
        cagr=cagr,
        max_drawdown=max_dd * 100,
        bars=len(bars),
    )


def _time_in_market(trades: list["ReplayTrade"], total_bars: int) -> Optional[float]:
    if total_bars <= 0:
        return None
    held = sum(t.holding_days for t in trades if t.holding_days is not None)
    return min(100.0, held / total_bars * 100)


def stats(
    trades: list[ReplayTrade], years: float, total_bars: int = 0,
    final_price: Optional[float] = None,
) -> ReplayStats:
    closed = [t for t in trades if t.pnl_pct is not None]
    # At most one position is open at a time (see `replay`), so at most one
    # trade in the list has `open_at_end`, and it is always the last one.
    open_trade = next((t for t in trades if t.open_at_end), None)
    rets = [t.pnl_pct / 100 for t in closed]
    wins = [t for t in closed if t.pnl_pct > 0]
    losses = [t for t in closed if t.pnl_pct <= 0]

    def avg(xs: list[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)

    # Mark a still-open position to the final close instead of dropping it.
    # Silently excluding it understates total_return_pct relative to
    # buy-and-hold, which by construction always captures the final price —
    # this would otherwise penalize whichever strategy happens to still be
    # holding at the window's end. It stays out of win_rate/avg_win/avg_loss
    # below: those describe trades that have actually realized, and an open
    # position hasn't.
    equity_has_data = bool(closed)
    if open_trade is not None and final_price is not None and open_trade.entry_price:
        equity *= 1 + (final_price / open_trade.entry_price - 1)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        equity_has_data = True

    trades_per_year = (len(closed) / years) if (closed and years > 0) else None
    mean = avg(rets)
    sharpe = None
    if mean is not None and len(rets) > 1 and trades_per_year:
        variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(variance)
        if sd > 0:
            sharpe = (mean / sd) * math.sqrt(trades_per_year)

    return ReplayStats(
        total_trades=len(closed),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(closed) * 100) if closed else None,
        cagr=((equity ** (1 / years) - 1) * 100) if years > 0 and equity > 0 else None,
        sharpe=sharpe,
        max_drawdown=(max_dd * 100) if equity_has_data else None,
        trades_per_year=trades_per_year,
        avg_win_pct=avg([t.pnl_pct for t in wins]),
        avg_loss_pct=avg([t.pnl_pct for t in losses]),
        time_in_market_pct=_time_in_market(trades, total_bars),
        total_return_pct=((equity - 1) * 100) if equity_has_data else None,
    )


# Below this, a backtest result cannot distinguish skill from luck. Reporting
# a Sharpe or CAGR on 12 trades without saying so is how people talk
# themselves into strategies that do not work.
MIN_MEANINGFUL_TRADES = 30


def significance_note(n_trades: int) -> str:
    if n_trades == 0:
        return "NO TRADES — nothing to evaluate."
    if n_trades < 10:
        return (
            f"n={n_trades}. FAR too few trades to mean anything. These numbers are "
            f"noise; a single trade dominates the result."
        )
    if n_trades < MIN_MEANINGFUL_TRADES:
        return (
            f"n={n_trades}. Below the ~{MIN_MEANINGFUL_TRADES}-trade threshold where "
            f"skill starts to be distinguishable from luck. Treat as suggestive only."
        )
    return f"n={n_trades}. Adequate sample for a preliminary read (not proof)."
