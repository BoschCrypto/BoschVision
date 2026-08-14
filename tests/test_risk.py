from hf_trading_bot import risk


def test_drawdown_pct_zero_above_high_water_mark():
    assert risk.drawdown_pct(110, 100) == 0.0


def test_drawdown_pct_negative_below_high_water_mark():
    assert risk.drawdown_pct(90, 100) == -10.0


def test_circuit_breaker_trips_at_threshold():
    state = risk.evaluate_circuit_breaker(equity=84, high_water_mark=100, max_drawdown_pct=15)
    assert state.tripped is True
    assert state.high_water_mark == 100


def test_circuit_breaker_not_tripped_within_threshold():
    state = risk.evaluate_circuit_breaker(equity=90, high_water_mark=100, max_drawdown_pct=15)
    assert state.tripped is False


def test_circuit_breaker_high_water_mark_ratchets_up():
    state = risk.evaluate_circuit_breaker(equity=120, high_water_mark=100, max_drawdown_pct=15)
    assert state.high_water_mark == 120
    assert state.tripped is False


def test_plan_stop_and_target_uses_atr_when_tighter():
    plan = risk.plan_stop_and_target(entry=100, atr=1, atr_multiple=2, stop_loss_pct=8, take_profit_r=3)
    assert plan.source == "atr"
    assert plan.stop == 98.0  # 100 - 2*1
    assert plan.target == 106.0  # 100 + 3*2


def test_plan_stop_and_target_clamps_to_pct_when_atr_wider():
    plan = risk.plan_stop_and_target(entry=100, atr=20, atr_multiple=2, stop_loss_pct=8, take_profit_r=3)
    assert plan.source == "pct"
    assert plan.stop == 92.0  # clamped to 8% of entry


def test_plan_stop_and_target_falls_back_to_pct_without_atr():
    plan = risk.plan_stop_and_target(entry=50, atr=None, atr_multiple=2, stop_loss_pct=10, take_profit_r=2)
    # No ATR available -> the pct distance is used as both distances, so the
    # tie goes to "atr" per the <= comparison (matches the ported TS logic).
    assert plan.source == "atr"
    assert plan.stop == 45.0


def test_allocate_risk_sized_buys_respects_max_open_positions():
    candidates = [
        risk.RiskCandidate(symbol="AAA", held_value=0, entry=100, stop=95),
        risk.RiskCandidate(symbol="BBB", held_value=0, entry=100, stop=95),
    ]
    allocations, skipped = risk.allocate_risk_sized_buys(
        candidates, equity=100_000, cash=100_000, exposure=0,
        risk_per_trade_pct=1, max_position_pct=50, exposure_cap_pct=100,
        open_positions=0, max_open_positions=1,
    )
    assert len(allocations) == 1
    assert allocations[0].symbol == "AAA"
    assert len(skipped) == 1
    assert "max concurrent open positions" in skipped[0].reason


def test_allocate_risk_sized_buys_sizes_by_risk_budget():
    candidates = [risk.RiskCandidate(symbol="AAA", held_value=0, entry=100, stop=95)]
    allocations, skipped = risk.allocate_risk_sized_buys(
        candidates, equity=100_000, cash=100_000, exposure=0,
        risk_per_trade_pct=1, max_position_pct=100, exposure_cap_pct=100,
        open_positions=0, max_open_positions=5,
    )
    assert not skipped
    # risk budget = 1% of 100k = 1000; risk/share = 5 -> qty = 200
    assert allocations[0].qty == 200
    assert allocations[0].risk_usd == 1000.0


def test_allocate_risk_sized_buys_skips_invalid_stop():
    candidates = [risk.RiskCandidate(symbol="AAA", held_value=0, entry=100, stop=100)]
    allocations, skipped = risk.allocate_risk_sized_buys(
        candidates, equity=100_000, cash=100_000, exposure=0,
        risk_per_trade_pct=1, max_position_pct=100, exposure_cap_pct=100,
        open_positions=0, max_open_positions=5,
    )
    assert not allocations
    assert "no valid stop distance" in skipped[0].reason


def test_evaluate_weekly_guards_blocks_on_loss_limit():
    state = risk.evaluate_weekly_guards(
        equity=93, week_start_equity=100, week_start_on="2026-08-10",
        max_weekly_loss_pct=6, consecutive_losses=0, max_consecutive_losses=3,
        weekly_loss_tripped_week=None, consecutive_loss_tripped_week=None,
        now=__import__("datetime").datetime(2026, 8, 12, tzinfo=__import__("datetime").timezone.utc),
    )
    assert state.week == "2026-08-10"
    assert state.weekly_loss_breached is True
    assert state.blocked is True


def test_evaluate_weekly_guards_resets_on_new_week():
    import datetime as dt

    state = risk.evaluate_weekly_guards(
        equity=93, week_start_equity=100, week_start_on="2026-08-03",
        max_weekly_loss_pct=6, consecutive_losses=0, max_consecutive_losses=3,
        weekly_loss_tripped_week=None, consecutive_loss_tripped_week=None,
        now=dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc),
    )
    # week_start_on is stale (prior week), so baseline resets to current equity
    assert state.week == "2026-08-10"
    assert state.week_start_equity == 93
    assert state.weekly_loss_breached is False


def test_last_n_business_days_skips_weekend():
    import datetime as dt

    # Wednesday 2026-08-12 -> back through Thu 8/6 (skips Sat 8/8, Sun 8/9)
    days = risk.last_n_business_days(5, dt.date(2026, 8, 12))
    assert days == [
        dt.date(2026, 8, 6),
        dt.date(2026, 8, 7),
        dt.date(2026, 8, 10),
        dt.date(2026, 8, 11),
        dt.date(2026, 8, 12),
    ]


def test_last_n_business_days_from_monday_reaches_prior_week():
    import datetime as dt

    days = risk.last_n_business_days(5, dt.date(2026, 8, 10))  # Monday
    assert days[-1] == dt.date(2026, 8, 10)
    assert days[0] == dt.date(2026, 8, 4)  # prior Tuesday
    assert all(d.weekday() < 5 for d in days)


def test_last_n_business_days_from_weekend_excludes_itself():
    import datetime as dt

    # Saturday ref date is not a business day, so it isn't in the window.
    days = risk.last_n_business_days(5, dt.date(2026, 8, 8))
    assert dt.date(2026, 8, 8) not in days
    assert days[-1] == dt.date(2026, 8, 7)  # Friday
    assert len(days) == 5


def test_evaluate_weekly_guards_consecutive_losses():
    state = risk.evaluate_weekly_guards(
        equity=100, week_start_equity=100, week_start_on="2026-08-10",
        max_weekly_loss_pct=6, consecutive_losses=3, max_consecutive_losses=3,
        weekly_loss_tripped_week=None, consecutive_loss_tripped_week=None,
        now=__import__("datetime").datetime(2026, 8, 12, tzinfo=__import__("datetime").timezone.utc),
    )
    assert state.consecutive_loss_breached is True
    assert state.blocked is True
