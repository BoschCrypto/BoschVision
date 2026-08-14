"""Core strategy engine: one evaluation cycle across the whole watchlist.

Order of operations, mirroring the guard hierarchy a live trading system
needs — protective exits must survive a pause, only new entries get halted:

  0. Stop-loss check   — force-close any held position whose stop was hit.
  1. Evaluate signals   — run each symbol's assigned strategy.
  2. Exits              — run even while the kill switch is on (a paused
                           system must still be able to protect open risk).
  3. Risk-sized entries  — only when no guard has halted new entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from hf_trading_bot import risk
from hf_trading_bot.broker.base import Broker
from hf_trading_bot.indicators import atr
from hf_trading_bot.storage import Storage
from hf_trading_bot.strategies.registry import evaluate


@dataclass
class CycleResult:
    ok: bool
    message: str = ""
    kill_switch: bool = False
    entries_blocked: bool = False
    exits_blocked: bool = False
    orders_placed: int = 0
    signals_logged: int = 0
    equity: float = 0.0
    log: list[str] = field(default_factory=list)


def _failed_backtest_strategies(storage: Storage, strategy_keys: set[str]) -> list[dict]:
    """Assigned strategies with a recorded sub-50% sweep hit rate.

    A hit rate below 50% is worse than a coin flip against buy-and-hold —
    see FINDINGS.md. Strategies never swept are not flagged here; this
    guards against deploying a strategy that has already been tested and
    rejected, not against deploying an unvalidated one.
    """
    failing = []
    for key in sorted(strategy_keys):
        result = storage.latest_sweep_result(key)
        if result is not None and result["hit_rate_pct"] < 50:
            failing.append(result)
    return failing


def run_strategy_cycle(
    broker: Broker, storage: Storage, dry_run: bool = False,
    allow_failed_backtest: bool = False,
) -> CycleResult:
    log: list[str] = []
    if dry_run:
        log.append("DRY RUN — no orders placed, no database writes.")

    settings = storage.get_settings()
    watchlist = storage.get_watchlist()
    if not watchlist:
        return CycleResult(ok=False, message="Watchlist is empty.")

    assignments = [w for w in watchlist if w["live_enabled"] and w["strategy_key"]]
    if not assignments:
        return CycleResult(ok=False, message="No symbols enabled for live execution.")
    symbols = [a["symbol"] for a in assignments]

    if not dry_run and not allow_failed_backtest:
        failing = _failed_backtest_strategies(storage, {a["strategy_key"] for a in assignments})
        if failing:
            names = ", ".join(
                f"{f['strategy_key']} ({f['hit_rate_pct']:.0f}% hit rate, "
                f"{f['median_excess_pts']:+.1f}pt median excess)" for f in failing
            )
            return CycleResult(
                ok=False,
                message=(
                    f"Refusing to trade live: {names} already tested below a coin flip "
                    f"against buy-and-hold in a recorded sweep (see FINDINGS.md / "
                    f"`hf-bot sweep-history`). Reassign the watchlist to a different "
                    f"strategy, or pass --i-know-this-failed-backtest to override."
                ),
            )

    kill_switch = bool(settings["kill_switch_active"])
    exits_allowed_when_paused = bool(settings["exits_allowed_when_paused"])

    account = broker.get_account()
    positions = broker.get_positions()
    bars = broker.get_daily_bars(symbols, 220)

    equity = account.equity
    cash = account.cash
    held = {p.symbol: p for p in positions}
    exposure = sum(abs(p.market_value) for p in positions)
    open_positions = len(positions)

    last_equity = account.last_equity or equity
    day_pnl_pct = (equity - last_equity) / last_equity * 100 if last_equity else 0.0
    daily_loss_breached = day_pnl_pct <= -abs(settings["max_daily_loss_pct"])

    breaker = risk.evaluate_circuit_breaker(
        equity, settings["equity_high_water_mark"], settings["max_drawdown_pct"]
    )
    log.append(
        f"Drawdown {breaker.drawdown_pct:.2f}% from high-water mark {breaker.high_water_mark:.2f} "
        f"(breaker at -{breaker.threshold_pct}%)."
    )

    weekly = risk.evaluate_weekly_guards(
        equity,
        settings["week_start_equity"],
        settings["week_start_on"],
        settings["max_weekly_loss_pct"],
        settings["consecutive_losses"],
        settings["max_consecutive_losses"],
        settings["weekly_loss_tripped_week"],
        settings["consecutive_loss_tripped_week"],
    )
    log.append(
        f"Week {weekly.week}: P&L {weekly.weekly_pnl_pct:.2f}% vs limit "
        f"-{abs(settings['max_weekly_loss_pct'])}%, streak "
        f"{settings['consecutive_losses']}/{settings['max_consecutive_losses']} losses."
    )

    entry_halts: list[str] = []
    if kill_switch:
        entry_halts.append("kill switch ON — paused")
    if daily_loss_breached:
        entry_halts.append("daily loss guard tripped")
        log.append(
            f"DAILY LOSS GUARD TRIPPED — day P&L {day_pnl_pct:.2f}% breached "
            f"-{abs(settings['max_daily_loss_pct'])}%. New entries halted."
        )
    if breaker.tripped:
        entry_halts.append("drawdown circuit breaker tripped")
        log.append(
            f"CIRCUIT BREAKER TRIPPED — drawdown {breaker.drawdown_pct:.2f}% breached "
            f"-{breaker.threshold_pct}% from high-water mark. New entries halted until manually reset."
        )
    if weekly.blocked:
        entry_halts.append(" + ".join(weekly.reasons))
        log.append(f"WEEKLY GUARD — {' + '.join(weekly.reasons)}. New entries halted for week {weekly.week}.")

    entries_blocked = len(entry_halts) > 0
    exits_blocked = kill_switch and not exits_allowed_when_paused
    log.append(
        "EXITS DISABLED — exits_allowed_when_paused is off and the kill switch is ON."
        if exits_blocked
        else "Exits ENABLED — protective sells run regardless of kill switch state."
    )

    if not dry_run:
        updates: dict = {
            "equity_high_water_mark": breaker.high_water_mark,
            "week_start_on": weekly.week,
        }
        if settings["week_start_on"] != weekly.week:
            updates["week_start_equity"] = equity
            updates["weekly_loss_tripped_week"] = None
            updates["consecutive_loss_tripped_week"] = None
            updates["consecutive_losses"] = 0
        if daily_loss_breached or breaker.tripped:
            updates["kill_switch_active"] = 1
        storage.update_settings(**updates)

    orders_placed = 0
    signals_logged = 0

    def push_signal(symbol: str, strategy_key: str, signal: str, detail: str, would_trade: bool) -> None:
        nonlocal signals_logged
        storage.log_signal(symbol, strategy_key, signal, detail, would_trade)
        signals_logged += 1

    # ---- Pass 0: stop-loss protection — the one exit that runs even when a
    # symbol's strategy hasn't fired an exit signal, because price already
    # breached the risk-sized stop recorded at entry. ------------------------
    stop_by_symbol: dict[str, float] = {}
    opened_at_by_symbol: dict[str, str] = {}
    for t in storage.recent_open_entries():
        if t["symbol"] not in stop_by_symbol and t["stop_price"] is not None:
            stop_by_symbol[t["symbol"]] = t["stop_price"]
        if t["symbol"] not in opened_at_by_symbol and t["opened_at"]:
            opened_at_by_symbol[t["symbol"]] = t["opened_at"]

    stopped_out: set[str] = set()
    if not exits_blocked and not dry_run:
        for symbol, position in list(held.items()):
            stop = stop_by_symbol.get(symbol)
            series = bars.get(symbol, [])
            if stop is None or not series or series[-1].l > stop:
                continue
            order = broker.place_order(symbol, position.qty, "sell")
            pnl = (stop - position.avg_entry_price) * position.qty
            storage.record_trade(
                symbol=symbol, side="sell", qty=position.qty, entry_price=position.avg_entry_price,
                exit_price=stop, pnl_usd=pnl, pnl_pct=(stop / position.avg_entry_price - 1) * 100,
                strategy_key="stop_loss", broker_order_id=order.id, status="filled", exit_reason="stop",
            )
            push_signal(symbol, "stop_loss", "exit", f"stop {stop} hit (session low {series[-1].l})", True)
            orders_placed += 1
            exposure -= abs(position.market_value)
            open_positions -= 1
            stopped_out.add(symbol)
            held.pop(symbol, None)
            log.append(f"{symbol}: STOP LOSS hit @ {stop} — SELL order submitted ({order.id})")

    # ---- Pass 1: evaluate every symbol's signal ----------------------------
    evaluated = []
    for a in assignments:
        if a["symbol"] in stopped_out:
            continue
        series = bars.get(a["symbol"], [])
        result = evaluate(a["strategy_key"], series, a["params"])
        position = held.get(a["symbol"])
        symbol_atr = atr(series, 14)
        action = None
        if result.signal == "entry" and not position:
            action = "buy"
        if result.signal == "exit" and position:
            action = "sell"
        evaluated.append(
            {
                "symbol": a["symbol"], "strategy_key": a["strategy_key"], "rank": a["rank"],
                "result": result, "atr": symbol_atr, "action": action, "position": position,
            }
        )

    for e in [x for x in evaluated if not x["action"]]:
        push_signal(e["symbol"], e["strategy_key"], e["result"].signal, e["result"].detail, False)
        log.append(f"{e['symbol']}: {e['result'].signal} — no action ({e['result'].detail})")

    # ---- PDT guard: count day trades used in the rolling 5-business-day ----
    # window. Robinhood (and every US broker) restricts accounts under $25k
    # to 3 day trades per rolling 5 business days — breaching it gets the
    # account flagged/restricted. Only SIGNAL exits are gated by this budget;
    # protective stop-loss exits (Pass 0, above) always execute regardless —
    # capital protection outranks a compliance flag.
    window = risk.last_n_business_days(5)
    max_day_trades = int(settings["max_day_trades"])
    day_trades_used = storage.day_trades_in_window(window[0].isoformat(), window[-1].isoformat())
    today_str = datetime.now(timezone.utc).date().isoformat()
    log.append(f"PDT: {day_trades_used}/{max_day_trades} day trades used in the last 5 business days.")

    # ---- Pass 2a: signal exits — run even while paused ---------------------
    for e in [x for x in evaluated if x["action"] == "sell"]:
        position = e["position"]
        detail = e["result"].detail
        if exits_blocked:
            push_signal(e["symbol"], e["strategy_key"], "exit", f"{detail} | would SELL — exits disabled", True)
            log.append(f"{e['symbol']}: would SELL — blocked, exits disabled")
            continue
        opened_today = opened_at_by_symbol.get(e["symbol"], "")[:10] == today_str
        if opened_today and day_trades_used >= max_day_trades and not dry_run:
            push_signal(
                e["symbol"], e["strategy_key"], "exit",
                f"{detail} | SELL blocked — PDT day-trade budget ({max_day_trades}/5 business days) exhausted, holding",
                True,
            )
            log.append(
                f"{e['symbol']}: SELL blocked — PDT day-trade budget exhausted this window; holding position "
                "(protective stops still execute regardless of this budget)."
            )
            continue
        if dry_run:
            push_signal(e["symbol"], e["strategy_key"], "exit", f"{detail} | DRY RUN would SELL", True)
            log.append(f"{e['symbol']}: DRY RUN would SELL")
            continue
        order = broker.place_order(e["symbol"], position.qty, "sell")
        pnl = position.unrealized_pl
        storage.record_trade(
            symbol=e["symbol"], side="sell", qty=position.qty, entry_price=position.avg_entry_price,
            exit_price=e["result"].price, pnl_usd=pnl, pnl_pct=position.unrealized_plpc * 100,
            strategy_key=e["strategy_key"], broker_order_id=order.id, status="filled", exit_reason="signal",
        )
        orders_placed += 1
        exposure -= abs(position.market_value)
        open_positions -= 1
        if opened_today:
            day_trades_used += 1
        push_signal(e["symbol"], e["strategy_key"], "exit", f"{detail} | SELL order {order.id}", True)
        log.append(f"{e['symbol']}: SELL order submitted ({order.id})")

    # ---- Pass 2b: risk-sized entries, only when entries are allowed --------
    buys = sorted([x for x in evaluated if x["action"] == "buy"], key=lambda x: x["rank"])
    if entries_blocked:
        reason = " + ".join(entry_halts)
        for e in buys:
            push_signal(e["symbol"], e["strategy_key"], "entry", f"{e['result'].detail} | would BUY ({reason})", True)
            log.append(f"{e['symbol']}: would BUY — blocked by {reason}")
    elif buys:
        plans = {}
        candidates = []
        for b in buys:
            entry = b["result"].price
            if not entry or entry <= 0:
                push_signal(b["symbol"], b["strategy_key"], "entry", f"{b['result'].detail} | BUY skipped (no price)", True)
                continue
            plan = risk.plan_stop_and_target(
                entry, b["atr"], settings["atr_stop_multiple"], settings["stop_loss_pct"], settings["take_profit_r"]
            )
            plans[b["symbol"]] = plan
            candidates.append(risk.RiskCandidate(symbol=b["symbol"], held_value=0.0, entry=entry, stop=plan.stop))

        allocations, skipped = risk.allocate_risk_sized_buys(
            candidates, equity, cash, exposure, settings["risk_per_trade_pct"],
            settings["max_position_pct"], settings["max_portfolio_exposure_pct"],
            open_positions, settings["max_open_positions"],
        )
        log.append(
            f"{len(buys)} entry signal(s) — risking {settings['risk_per_trade_pct']}% of equity per trade, "
            f"{len(allocations)} sized, {open_positions}/{settings['max_open_positions']} slots used."
        )

        for sk in skipped:
            e = next(b for b in buys if b["symbol"] == sk.symbol)
            push_signal(e["symbol"], e["strategy_key"], "entry", f"{e['result'].detail} | BUY skipped ({sk.reason})", True)
            log.append(f"{e['symbol']}: BUY skipped — {sk.reason}")

        for alloc in allocations:
            e = next(b for b in buys if b["symbol"] == alloc.symbol)
            plan = plans[alloc.symbol]
            # Fractional shares: size by dollar notional, not whole shares —
            # Robinhood (and PaperBroker) both accept fractional quantities,
            # so the only real floor is the broker's minimum order notional.
            qty = alloc.qty
            if alloc.notional < 1.0:
                push_signal(e["symbol"], e["strategy_key"], "entry", f"{e['result'].detail} | BUY skipped (sized below $1 minimum)", True)
                log.append(f"{e['symbol']}: BUY skipped — risk-sized notional (${alloc.notional:.2f}) below $1 minimum")
                continue
            if dry_run:
                push_signal(
                    e["symbol"], e["strategy_key"], "entry",
                    f"{e['result'].detail} | DRY RUN would BUY {qty:.4f} @ {e['result'].price} stop {plan.stop} target {plan.target}",
                    True,
                )
                log.append(f"{e['symbol']}: DRY RUN would BUY {qty:.4f} sh stop {plan.stop} target {plan.target}")
                continue
            order = broker.place_order(e["symbol"], qty, "buy", stop_price=plan.stop)
            storage.record_trade(
                symbol=e["symbol"], side="buy", qty=qty, entry_price=e["result"].price, exit_price=None,
                pnl_usd=None, pnl_pct=None, strategy_key=e["strategy_key"], broker_order_id=order.id,
                status="filled", stop_price=plan.stop, take_profit_price=plan.target,
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            orders_placed += 1
            push_signal(
                e["symbol"], e["strategy_key"], "entry",
                f"{e['result'].detail} | BUY {qty:.4f} stop {plan.stop} target {plan.target} order {order.id}", True,
            )
            log.append(
                f"{e['symbol']}: BUY {qty:.4f} sh submitted — stop {plan.stop} ({plan.source}), "
                f"target {plan.target}, risk ${alloc.risk_usd} ({order.id})"
            )

    if not dry_run:
        storage.record_equity_snapshot(equity, cash)

    return CycleResult(
        ok=True,
        kill_switch=kill_switch or daily_loss_breached or breaker.tripped,
        entries_blocked=entries_blocked or daily_loss_breached or breaker.tripped,
        exits_blocked=exits_blocked,
        orders_placed=orders_placed,
        signals_logged=signals_logged,
        equity=equity,
        log=log,
    )
