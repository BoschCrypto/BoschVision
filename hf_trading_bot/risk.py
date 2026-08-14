"""Pure portfolio-risk math. No I/O — kept separate from the strategy engine
so the circuit breaker and sizing rules can be unit-tested in isolation."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional


def drawdown_pct(equity: float, high_water_mark: float) -> float:
    """Drawdown from the high-water mark, as a negative percentage (0 when at/above HWM)."""
    if not high_water_mark or high_water_mark <= 0:
        return 0.0
    if equity >= high_water_mark:
        return 0.0
    return (equity - high_water_mark) / high_water_mark * 100


@dataclass
class CircuitBreakerState:
    high_water_mark: float
    drawdown_pct: float
    tripped: bool
    threshold_pct: float


def evaluate_circuit_breaker(
    equity: float, high_water_mark: Optional[float], max_drawdown_pct: float
) -> CircuitBreakerState:
    """Portfolio max-drawdown circuit breaker. The high-water mark ratchets up
    with equity and never down; a drawdown at or beyond the threshold trips it."""
    threshold = abs(max_drawdown_pct or 0)
    hwm = max(high_water_mark or 0, equity)
    dd = drawdown_pct(equity, max(high_water_mark or 0, 0))
    return CircuitBreakerState(
        high_water_mark=hwm,
        drawdown_pct=dd,
        threshold_pct=threshold,
        tripped=threshold > 0 and dd <= -threshold,
    )


@dataclass
class StopPlan:
    stop: float
    target: float
    risk_per_share: float
    source: str  # "atr" | "pct"


def _round2(n: float) -> float:
    return round(n, 2)


def plan_stop_and_target(
    entry: float,
    atr: Optional[float],
    atr_multiple: float,
    stop_loss_pct: float,
    take_profit_r: float,
) -> StopPlan:
    """Stop = entry - atr_multiple x ATR(14), hard-clamped so the distance
    never exceeds stop_loss_pct of entry (illiquid ATR spikes can't widen the
    stop). Target = entry + take_profit_r x risk-per-share."""
    pct_distance = entry * abs(stop_loss_pct) / 100
    atr_distance = atr * abs(atr_multiple) if atr and atr > 0 else pct_distance
    distance = max(0.01, min(atr_distance, pct_distance))
    stop = _round2(max(0.01, entry - distance))
    risk_per_share = max(0.01, entry - stop)
    return StopPlan(
        stop=stop,
        target=_round2(entry + risk_per_share * abs(take_profit_r)),
        risk_per_share=risk_per_share,
        source="atr" if atr_distance <= pct_distance else "pct",
    )


def r_multiple(entry: float, stop: float, current: float) -> float:
    """Realised R multiple of an open position."""
    risk = entry - stop
    if not risk > 0:
        return 0.0
    return (current - entry) / risk


def should_activate_trail(r: float, trail_activate_r: float) -> bool:
    return trail_activate_r > 0 and r >= trail_activate_r


def holding_days(opened_at: datetime, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return (now - opened_at).days


def time_stop_exceeded(
    opened_at: Optional[datetime], max_days: float, now: Optional[datetime] = None
) -> bool:
    if not opened_at or not (max_days > 0):
        return False
    return holding_days(opened_at, now) >= max_days


@dataclass
class RiskCandidate:
    symbol: str
    held_value: float
    entry: float
    stop: float


@dataclass
class Allocation:
    symbol: str
    notional: float
    qty: float = 0.0
    risk_usd: float = 0.0


@dataclass
class SkippedAllocation:
    symbol: str
    reason: str


def allocate_risk_sized_buys(
    candidates: list[RiskCandidate],
    equity: float,
    cash: float,
    exposure: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
    exposure_cap_pct: float,
    open_positions: int,
    max_open_positions: int,
) -> tuple[list[Allocation], list[SkippedAllocation]]:
    """qty = (equity x risk_per_trade_pct) / (entry - stop). The resulting
    notional is then clamped by the per-symbol cap, remaining exposure room,
    available cash and the max concurrent position limit."""
    skipped: list[SkippedAllocation] = []
    slots = max(0, max_open_positions - open_positions)
    accepted = candidates[:slots]
    for c in candidates[slots:]:
        skipped.append(
            SkippedAllocation(c.symbol, f"max concurrent open positions ({max_open_positions}) reached")
        )

    max_position_notional = equity * max_position_pct / 100
    exposure_cap = equity * exposure_cap_pct / 100
    room = min(max(0.0, exposure_cap - exposure), max(0.0, cash))
    risk_budget = equity * abs(risk_per_trade_pct) / 100

    allocations: list[Allocation] = []
    for c in accepted:
        risk_per_share = c.entry - c.stop
        if not (risk_per_share > 0) or not (c.entry > 0):
            skipped.append(SkippedAllocation(c.symbol, "no valid stop distance — cannot risk-size"))
            continue
        raw_qty = risk_budget / risk_per_share
        symbol_room = max(0.0, max_position_notional - c.held_value)
        notional = math.floor(min(raw_qty * c.entry, symbol_room, room) * 100) / 100
        if notional < 1:
            reason = (
                f"per-symbol cap {max_position_pct}% of equity reached"
                if symbol_room <= room
                else f"portfolio exposure cap {exposure_cap_pct}% / buying power exhausted"
            )
            skipped.append(SkippedAllocation(c.symbol, reason))
            continue
        qty = notional / c.entry
        room -= notional
        allocations.append(
            Allocation(symbol=c.symbol, notional=notional, qty=qty, risk_usd=round(qty * risk_per_share, 2))
        )
    return allocations, skipped


def last_n_business_days(n: int, ref_date: Optional[date] = None) -> list[date]:
    """The last `n` business days (Mon-Fri), ending on and including
    `ref_date` (today in UTC if not given). Returned oldest-first, so
    `result[0]` / `result[-1]` are the window's start/end dates. Used for the
    PDT rolling 5-business-day day-trade count — no holiday calendar, just
    weekend-skipping, which is an acceptable v1 simplification."""
    ref_date = ref_date or datetime.now(timezone.utc).date()
    days: list[date] = []
    d = ref_date
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def week_start(d: Optional[datetime] = None) -> str:
    """ISO date (YYYY-MM-DD) of the Monday starting the week containing `d`."""
    d = d or datetime.now(timezone.utc)
    delta = d.isoweekday() - 1
    monday = (d - timedelta(days=delta)).date()
    return monday.isoformat()


@dataclass
class WeeklyGuardState:
    week: str
    week_start_equity: float
    weekly_pnl_pct: float
    weekly_loss_breached: bool
    consecutive_loss_breached: bool
    blocked: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_weekly_guards(
    equity: float,
    week_start_equity: Optional[float],
    week_start_on: Optional[str],
    max_weekly_loss_pct: float,
    consecutive_losses: int,
    max_consecutive_losses: int,
    weekly_loss_tripped_week: Optional[str],
    consecutive_loss_tripped_week: Optional[str],
    now: Optional[datetime] = None,
) -> WeeklyGuardState:
    """Weekly loss limit and consecutive-loss limit. Both block NEW ENTRIES
    for the remainder of the week and auto-reset on Monday."""
    week = week_start(now)
    same_week = week_start_on == week
    baseline = week_start_equity if (same_week and week_start_equity) else equity
    weekly_pnl_pct = (equity - baseline) / baseline * 100 if baseline > 0 else 0.0

    threshold = abs(max_weekly_loss_pct or 0)
    weekly_loss_breached = (
        threshold > 0 and weekly_pnl_pct <= -threshold
    ) or weekly_loss_tripped_week == week

    max_streak = max_consecutive_losses or 0
    consecutive_loss_breached = (
        max_streak > 0 and consecutive_losses >= max_streak
    ) or consecutive_loss_tripped_week == week

    reasons: list[str] = []
    if weekly_loss_breached:
        reasons.append(f"weekly loss limit -{threshold}% hit")
    if consecutive_loss_breached:
        reasons.append(f"{max_streak} consecutive losses hit")

    return WeeklyGuardState(
        week=week,
        week_start_equity=baseline,
        weekly_pnl_pct=weekly_pnl_pct,
        weekly_loss_breached=weekly_loss_breached,
        consecutive_loss_breached=consecutive_loss_breached,
        blocked=weekly_loss_breached or consecutive_loss_breached,
        reasons=reasons,
    )
