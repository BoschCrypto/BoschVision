from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

import click
from dotenv import load_dotenv

from hf_trading_bot.backtest import buy_and_hold, replay, significance_note, stats
from hf_trading_bot.config import AppConfig
from hf_trading_bot.data.provider import get_provider, source_of
from hf_trading_bot.engine import run_strategy_cycle
from hf_trading_bot.storage import Storage
from hf_trading_bot.strategies.registry import STRATEGY_KEYS

load_dotenv()


def _build_provider(cfg: AppConfig):
    try:
        return get_provider(cfg.data_provider)
    except ValueError as e:
        raise click.ClickException(str(e)) from e


def _build_broker(cfg: AppConfig):
    provider = _build_provider(cfg)
    if cfg.broker == "paper":
        from hf_trading_bot.broker.paper import PaperBroker

        return PaperBroker(starting_cash=cfg.starting_cash, data_provider=provider)
    if cfg.broker == "alpaca":
        from hf_trading_bot.broker.alpaca import AlpacaBroker

        return AlpacaBroker(data_provider=provider)
    if cfg.broker == "robinhood":
        from hf_trading_bot.broker.robinhood import RobinhoodBroker

        return RobinhoodBroker(data_provider=provider)
    raise click.ClickException(f"Unknown broker: {cfg.broker}")


def _load_storage(cfg: AppConfig) -> Storage:
    storage = Storage(cfg.db_path)
    if cfg.risk:
        storage.update_settings(**cfg.risk)
    if cfg.watchlist:
        for i, w in enumerate(cfg.watchlist):
            storage.upsert_watchlist_symbol(w["symbol"], w["strategy_key"], params=w.get("params"), rank=i)
    else:
        storage.seed_default_watchlist()
    return storage


@click.group()
@click.option("--config", "config_path", default=None, help="Path to settings.yaml")
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]):
    """Systematic trading bot — simulation by default, with Alpaca paper
    trading, backtests, and opt-in live adapters behind explicit guards.

    No strategy here is guaranteed to be profitable. Validate on backtests
    and paper trading before ever considering live trading.
    """
    ctx.obj = AppConfig.load(config_path)


@cli.command()
@click.option("--dry-run/--live", default=True, help="Dry run (no orders/db writes) or actually execute.")
@click.option("--i-know-this-failed-backtest", "allow_failed_backtest", is_flag=True,
              help="Override the guard that blocks live trading on a strategy with a "
                   "recorded sub-50% sweep hit rate.")
@click.pass_obj
def run(cfg: AppConfig, dry_run: bool, allow_failed_backtest: bool):
    """Run one strategy evaluation cycle against the configured broker."""
    storage = _load_storage(cfg)
    broker = _build_broker(cfg)
    result = run_strategy_cycle(broker, storage, dry_run=dry_run, allow_failed_backtest=allow_failed_backtest)
    for line in result.log:
        click.echo(line)
    if not result.ok:
        storage.close()
        raise click.ClickException(result.message)
    click.echo(
        f"\nEquity: ${result.equity:,.2f} | orders placed: {result.orders_placed} | "
        f"signals logged: {result.signals_logged}"
    )
    storage.close()


@cli.command()
@click.option("--interval", default=3600, help="Seconds between cycles (default: hourly).")
@click.option("--dry-run/--live", default=True)
@click.option("--i-know-this-failed-backtest", "allow_failed_backtest", is_flag=True,
              help="Override the guard that blocks live trading on a strategy with a "
                   "recorded sub-50% sweep hit rate.")
@click.pass_obj
def loop(cfg: AppConfig, interval: int, dry_run: bool, allow_failed_backtest: bool):
    """Run strategy cycles repeatedly on a fixed interval until interrupted.

    This is a systematic-trading loop (seconds-to-hours cadence), not true
    high-frequency trading — that requires colocated infrastructure this
    tool does not provide.
    """
    storage = _load_storage(cfg)
    broker = _build_broker(cfg)
    click.echo(f"Looping every {interval}s ({'DRY RUN' if dry_run else 'LIVE'}). Ctrl+C to stop.")
    try:
        while True:
            result = run_strategy_cycle(
                broker, storage, dry_run=dry_run, allow_failed_backtest=allow_failed_backtest
            )
            status = "ok" if result.ok else f"error: {result.message}"
            click.echo(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {status} — "
                f"orders {result.orders_placed}, equity ${result.equity:,.2f}"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Stopped.")
    finally:
        storage.close()


@cli.command("alpaca-check")
@click.pass_obj
def alpaca_check(cfg: AppConfig):
    """Verify Alpaca credentials and report account + market state.

    Reads ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY from your environment or
    .env file. Unlike Robinhood, Alpaca uses stateless API keys — there's no
    MFA step and no session file to persist.
    """
    from hf_trading_bot.broker.alpaca import AlpacaBroker

    broker = AlpacaBroker(data_provider=_build_provider(cfg))
    mode = "PAPER (simulated money)" if broker.is_paper else "*** LIVE — REAL MONEY ***"
    click.echo(f"Mode:    {mode}")
    click.echo(f"Account: {broker.get_account_number()}")

    account = broker.get_account()
    click.echo(f"Equity:  ${account.equity:,.2f}")
    click.echo(f"Cash:    ${account.cash:,.2f}   Buying power: ${account.buying_power:,.2f}")

    clock = broker.get_clock()
    is_open = clock.get("is_open")
    click.echo(f"Market:  {'OPEN' if is_open else 'CLOSED'}")
    if not is_open and clock.get("next_open"):
        click.echo(f"         next open: {clock['next_open']}")

    positions = broker.get_positions()
    click.echo(f"Positions: {len(positions)}")
    for p in positions:
        click.echo(f"  {p.symbol:6s} {p.qty:>10.4f} @ ${p.avg_entry_price:.2f}  P&L ${p.unrealized_pl:+,.2f}")


@cli.command("robinhood-login")
def robinhood_login():
    """One-time interactive login: establishes a persisted Robinhood session.

    Run this once, by hand, on the machine that will actually run `hf-bot
    loop --live` against broker: robinhood — it will prompt for your MFA
    code interactively. Requires ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD, and
    HF_BOT_I_UNDERSTAND_LIVE_TRADING=true (via .env or the environment).
    Subsequent runs reuse the saved session (~/.tokens/robinhood.pickle) and
    should not need MFA again unless that file is lost or Robinhood forces
    re-verification.
    """
    from hf_trading_bot.broker.robinhood import RobinhoodBroker

    RobinhoodBroker()
    click.echo("Robinhood session established and saved to ~/.tokens/robinhood.pickle.")
    click.echo("Keep that file as secret as your password — anyone with it can trade on your account.")


@cli.command("kill-switch")
@click.option("--on/--off", "active", required=True)
@click.pass_obj
def kill_switch_cmd(cfg: AppConfig, active: bool):
    """Turn the kill switch on (paused) or off (trading enabled)."""
    storage = _load_storage(cfg)
    storage.set_kill_switch(active)
    click.echo(f"Kill switch is now {'ON (paused)' if active else 'OFF (trading enabled)'}.")
    storage.close()


@cli.command()
@click.pass_obj
def status(cfg: AppConfig):
    """Show current settings, watchlist, and kill-switch state."""
    storage = _load_storage(cfg)
    settings = storage.get_settings()
    click.echo(f"Kill switch: {'ON (paused)' if settings['kill_switch_active'] else 'OFF (trading enabled)'}")
    click.echo(f"High-water mark: ${settings['equity_high_water_mark']:,.2f}")
    click.echo("Watchlist:")
    for w in storage.get_watchlist():
        click.echo(f"  {w['symbol']:6s} {w['strategy_key']:20s} {'enabled' if w['live_enabled'] else 'disabled'}")
    storage.close()


@cli.command()
@click.argument("symbol")
@click.option("--strategy", "strategy_key", type=click.Choice(STRATEGY_KEYS), default="momentum_90d")
@click.option("--start", default="2021-01-01")
@click.option("--end", default=None)
@click.option("--benchmark", default="SPY", help="Benchmark symbol (default SPY). Use '' to skip.")
@click.pass_obj
def backtest(
    cfg: AppConfig, symbol: str, strategy_key: str, start: str, end: Optional[str], benchmark: str
):
    """Backtest one symbol/strategy, always against buy-and-hold benchmarks.

    A strategy's return in isolation says nothing. The only number that
    matters is how it compares to (a) simply holding the same stock, and
    (b) holding the index. Both are always reported.
    """
    provider = _build_provider(cfg)
    bars = provider.daily_bars_range(symbol, start, end)
    if len(bars) < 60:
        raise click.ClickException(f"Not enough historical bars for {symbol} in that range.")
    first, last = bars[0].t[:10], bars[-1].t[:10]
    years = (_dt.date.fromisoformat(last) - _dt.date.fromisoformat(first)).days / 365.25
    trades = replay(bars, strategy_key, {})
    s = stats(trades, years, total_bars=len(bars), final_price=bars[-1].c)

    click.echo(f"\n{symbol} / {strategy_key}   {first} → {last}  ({years:.1f}y, {len(bars)} bars)")
    click.echo(f"Data source: {source_of(provider)}")
    click.echo("-" * 68)

    if s.total_trades == 0:
        click.echo("  No trades generated — nothing to evaluate.")
        return

    # ---- strategy ----
    click.echo(f"{'STRATEGY':<26} {'return':>10} {'CAGR':>9} {'maxDD':>9} {'Sharpe':>8}")
    sharpe_txt = f"{s.sharpe:>8.2f}" if s.sharpe is not None else f"{'n/a':>8}"
    click.echo(
        f"{strategy_key:<26} {s.total_return_pct:>9.1f}% {s.cagr:>8.1f}% "
        f"{s.max_drawdown:>8.1f}% {sharpe_txt}"
    )

    # ---- benchmarks ----
    bh = buy_and_hold(bars, symbol, years)
    benches = []
    if bh:
        benches.append(bh)
        click.echo(
            f"{'buy & hold ' + symbol:<26} {bh.total_return_pct:>9.1f}% {bh.cagr:>8.1f}% "
            f"{bh.max_drawdown:>8.1f}% {'—':>8}"
        )
    if benchmark and benchmark.upper() != symbol.upper():
        try:
            bbars = provider.daily_bars_range(benchmark, start, end)
            bstat = buy_and_hold(bbars, benchmark.upper(), years)
            if bstat:
                benches.append(bstat)
                click.echo(
                    f"{'buy & hold ' + benchmark.upper():<26} {bstat.total_return_pct:>9.1f}% "
                    f"{bstat.cagr:>8.1f}% {bstat.max_drawdown:>8.1f}% {'—':>8}"
                )
        except Exception as e:  # noqa: BLE001 — benchmark is informational
            click.echo(f"  (benchmark {benchmark} unavailable: {e})")

    # ---- the verdict ----
    click.echo("-" * 68)
    for b in benches:
        excess = s.total_return_pct - b.total_return_pct
        verdict = "BEAT" if excess > 0 else "LOST TO"
        click.echo(f"  vs buy & hold {b.symbol:<6} {verdict:>8}  by {excess:>+8.1f} pts")

    if s.time_in_market_pct is not None:
        click.echo(f"  Time in market: {s.time_in_market_pct:.0f}%  (rest in cash)")
    click.echo(f"  Trades: {s.total_trades}   Win rate: {s.win_rate:.1f}%")
    still_open = sum(1 for t in trades if t.open_at_end)
    if still_open:
        click.echo(
            f"  NOTE: {still_open} position(s) still open at window end — their "
            f"unrealised P&L is EXCLUDED from the numbers above."
        )
    click.echo(f"\n  {significance_note(s.total_trades)}")
    if benches and all(s.total_return_pct < b.total_return_pct for b in benches):
        click.echo(
            "\n  This strategy underperformed simply buying and holding. "
            "On this evidence it destroyed value."
        )


@cli.group()
def journal():
    """Decision journal — the written record of why each position was taken.

    Contemporaneous notes are the only defence against hindsight bias.
    Winners feel inevitable in retrospect and losers feel unlucky; only what
    you wrote down beforehand tells you which it actually was.
    """


@journal.command("add")
@click.option("--symbol", required=True)
@click.option("--decision", required=True, type=click.Choice(["BUY", "SELL", "HOLD", "PASS"], case_sensitive=False))
@click.option("--conviction", default="medium", type=click.Choice(["low", "medium", "high"]))
@click.option("--thesis", required=True, help="Why is this worth owning? 3 sentences.")
@click.option("--falsification", default="", help="What would prove this WRONG? Required for BUY/SELL.")
@click.option("--red-team", default=None, help="The strongest objection, verbatim.")
@click.option("--benchmark-thesis", default=None, help="Why this beats just buying the index.")
@click.option("--entry", type=float, default=None)
@click.option("--stop", type=float, default=None)
@click.option("--target", type=float, default=None)
@click.option("--size-pct", type=float, default=None)
@click.pass_obj
def journal_add(cfg, symbol, decision, conviction, thesis, falsification, red_team,
                benchmark_thesis, entry, stop, target, size_pct):
    """Record a decision. Refuses BUY/SELL without falsification criteria."""
    from hf_trading_bot.journal import Decision, Journal, JournalError

    storage = _load_storage(cfg)
    j = Journal(storage._conn)
    try:
        did = j.record(Decision(
            symbol=symbol, decision=decision, conviction=conviction, thesis=thesis,
            falsification=falsification, red_team_objection=red_team,
            benchmark_thesis=benchmark_thesis, entry_price=entry, stop_price=stop,
            target_price=target, position_pct=size_pct,
        ))
    except JournalError as e:
        storage.close()
        raise click.ClickException(str(e)) from e
    click.echo(f"Recorded decision #{did}: {decision.upper()} {symbol.upper()}")
    storage.close()


@journal.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include closed/reviewed decisions.")
@click.pass_obj
def journal_list(cfg, show_all):
    """Show open theses (or everything with --all)."""
    from hf_trading_bot.journal import Journal

    storage = _load_storage(cfg)
    j = Journal(storage._conn)
    rows = j.all_decisions() if show_all else j.open_decisions()
    if not rows:
        click.echo("No decisions recorded yet." if show_all else "No open theses.")
        storage.close()
        return
    for r in rows:
        status = r["outcome"] or "OPEN"
        click.echo(f"\n#{r['id']} {r['decision']} {r['symbol']}  [{r['conviction']}]  {status}")
        click.echo(f"   decided: {r['decided_at'][:10]}")
        click.echo(f"   thesis:  {r['thesis'][:120]}")
        if r["falsification"]:
            click.echo(f"   wrong if: {r['falsification'][:120]}")
    storage.close()


@journal.command("review")
@click.argument("decision_id", type=int)
@click.option("--outcome", required=True, type=click.Choice(["right", "wrong", "unresolved"]))
@click.option("--exit-price", type=float, default=None)
@click.option("--pnl-pct", type=float, default=None)
@click.option("--followed-rules/--broke-rules", default=None,
              help="Did you exit when your own falsification criteria triggered?")
@click.option("--lessons", default=None)
@click.pass_obj
def journal_review(cfg, decision_id, outcome, exit_price, pnl_pct, followed_rules, lessons):
    """Close out a decision and record what actually happened."""
    from hf_trading_bot.journal import Journal, JournalError

    storage = _load_storage(cfg)
    j = Journal(storage._conn)
    try:
        j.review(decision_id, outcome, exit_price, pnl_pct, followed_rules, lessons)
    except JournalError as e:
        storage.close()
        raise click.ClickException(str(e)) from e
    click.echo(f"Decision #{decision_id} reviewed: {outcome}")
    storage.close()


@journal.command("scorecard")
@click.pass_obj
def journal_scorecard(cfg):
    """Your calibration over time — the honest self-assessment."""
    from hf_trading_bot.journal import Journal

    storage = _load_storage(cfg)
    j = Journal(storage._conn)
    s = j.scorecard()
    if not s.get("reviewed"):
        click.echo("No reviewed decisions yet. Come back after closing some positions.")
        storage.close()
        return
    click.echo(f"\nReviewed decisions: {s['reviewed']}")
    click.echo(f"Accuracy:           {s['accuracy_pct']:.0f}%")
    if s["discipline_pct"] is not None:
        click.echo(f"Followed own rules: {s['discipline_pct']:.0f}%   <- matters more than accuracy")
    if s["avg_pnl_pct"] is not None:
        click.echo(f"Average P&L:        {s['avg_pnl_pct']:+.1f}%")
    click.echo("\nAccuracy by stated conviction (are you calibrated?):")
    for conv, b in sorted(s["by_conviction"].items()):
        pct = b["right"] / b["n"] * 100 if b["n"] else 0
        click.echo(f"  {conv:<7} {b['right']}/{b['n']}  ({pct:.0f}%)")
    click.echo(
        "\nIf 'high' conviction is not markedly more accurate than 'low', your\n"
        "confidence carries no information — size all positions equally until\n"
        "that changes."
    )
    storage.close()


DEFAULT_UNIVERSE = [
    # Deliberately mixed: mega-cap tech, broad index, cyclicals, defensives,
    # and names that did BADLY over recent years. A universe of only winners
    # is survivorship bias and will make any long-biased strategy look good.
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "SPY", "QQQ", "IWM",
    "JPM", "XOM", "JNJ", "PG", "KO", "WMT",
    "NKE", "DIS", "INTC", "PYPL", "PFE", "BA", "T", "VZ",
]


@cli.command()
@click.option("--symbols", default=None, help="Comma-separated. Defaults to a mixed 24-name universe.")
@click.option("--strategies", default=None, help="Comma-separated. Defaults to all.")
@click.option("--start", default="2021-01-01")
@click.option("--end", default=None)
@click.pass_obj
def sweep(cfg: AppConfig, symbols, strategies, start, end):
    """Run every strategy across a universe and report whether ANY of them
    actually beat buy-and-hold.

    This is the honest test. A single backtest on one symbol that happened to
    go up tells you nothing — run enough symbols and one will look brilliant
    by chance alone. What matters is the hit rate across a mixed universe
    that includes losers.
    """
    provider = _build_provider(cfg)
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else DEFAULT_UNIVERSE
    strats = [s.strip() for s in strategies.split(",")] if strategies else STRATEGY_KEYS

    click.echo(f"\nSweeping {len(strats)} strategies × {len(syms)} symbols ({start} → {end or 'today'})")
    click.echo("Fetching data...\n")

    results: dict[str, list[dict]] = {k: [] for k in strats}
    bh_by_symbol: dict[str, float] = {}
    failed: list[str] = []

    for sym in syms:
        try:
            bars = provider.daily_bars_range(sym, start, end)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{sym} ({e})")
            continue
        if len(bars) < 60:
            failed.append(f"{sym} (only {len(bars)} bars)")
            continue

        first, last = bars[0].t[:10], bars[-1].t[:10]
        years = (_dt.date.fromisoformat(last) - _dt.date.fromisoformat(first)).days / 365.25
        bh = buy_and_hold(bars, sym, years)
        if not bh:
            continue
        bh_by_symbol[sym] = bh.total_return_pct

        for key in strats:
            s = stats(replay(bars, key, {}), years, total_bars=len(bars), final_price=bars[-1].c)
            if s.total_trades == 0:
                continue
            results[key].append(
                {
                    "symbol": sym,
                    "ret": s.total_return_pct,
                    "excess": s.total_return_pct - bh.total_return_pct,
                    "trades": s.total_trades,
                    "dd": s.max_drawdown,
                }
            )
        click.echo(f"  {sym:<6} done  (buy&hold {bh.total_return_pct:+.0f}%)")

    if failed:
        click.echo(f"\nSkipped: {', '.join(failed)}")

    # ---- aggregate verdict, the part that matters -------------------------
    click.echo("\n" + "=" * 78)
    click.echo("AGGREGATE — did the strategy beat simply holding the same stock?")
    click.echo("=" * 78)
    click.echo(f"{'strategy':<22} {'beat B&H':>10} {'hit rate':>10} {'median excess':>15} {'trades':>8}")
    click.echo("-" * 78)

    run_id = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    storage = _load_storage(cfg)

    verdicts = []
    for key in strats:
        rows = results[key]
        if not rows:
            click.echo(f"{key:<22} {'no trades':>10}")
            continue
        wins = sum(1 for r in rows if r["excess"] > 0)
        excesses = sorted(r["excess"] for r in rows)
        median = excesses[len(excesses) // 2]
        total_trades = sum(r["trades"] for r in rows)
        hit = wins / len(rows) * 100
        click.echo(
            f"{key:<22} {f'{wins}/{len(rows)}':>10} {hit:>9.0f}% {median:>14.1f}pts {total_trades:>8}"
        )
        verdicts.append((key, hit, median, total_trades))
        storage.record_sweep_result(
            run_id=run_id, strategy_key=key, symbols_tested=len(rows), wins=wins,
            hit_rate_pct=hit, median_excess_pts=median, total_trades=total_trades,
            window_start=start, window_end=end,
        )

    storage.close()
    if verdicts:
        click.echo(f"\nSaved as sweep run {run_id} (`hf-bot sweep-history` to review).")

    click.echo("\n" + "-" * 78)
    click.echo("HOW TO READ THIS")
    click.echo("-" * 78)
    click.echo(
        "  A coin flip is a ~50% hit rate. A strategy needs to beat buy-and-hold on\n"
        "  clearly MORE than half the universe, with positive median excess return,\n"
        "  before there is any reason to believe it adds value. Anything at or below\n"
        "  50% is evidence the strategy is noise — or actively harmful after costs\n"
        "  and short-term capital gains tax."
    )
    if verdicts:
        best = max(verdicts, key=lambda v: v[1])
        click.echo("")
        if best[1] > 60 and best[2] > 0:
            click.echo(
                f"  Best: {best[0]} beat buy-and-hold {best[1]:.0f}% of the time "
                f"(median {best[2]:+.1f} pts).\n"
                f"  Worth further out-of-sample testing — NOT yet worth real money."
            )
        else:
            click.echo(
                f"  No strategy cleared the bar. Best was {best[0]} at {best[1]:.0f}% hit rate,\n"
                f"  median excess {best[2]:+.1f} pts. On this evidence, these strategies do not\n"
                f"  beat simply buying and holding — and that is the honest, useful result."
            )


@cli.command("sweep-history")
@click.option("--strategy", "strategy_key", default=None,
              help="Show only this strategy's recorded runs.")
@click.option("--limit", default=20, help="Max rows to show.")
@click.pass_obj
def sweep_history(cfg, strategy_key, limit):
    """Every sweep verdict ever recorded, so a rejected strategy stays rejected.

    Without this, an uncomfortable result quietly softens into "roughly
    break-even" after a few months, and the same question gets re-litigated
    (and re-paid for) instead of staying answered.
    """
    storage = _load_storage(cfg)
    rows = storage.all_sweep_results(limit=limit)
    storage.close()
    if strategy_key:
        rows = [r for r in rows if r["strategy_key"] == strategy_key]
    if not rows:
        click.echo("No sweep runs recorded yet. Run `hf-bot sweep` first.")
        return

    click.echo(f"{'run_at':<21} {'strategy':<20} {'hit rate':>9} {'median excess':>14} {'trades':>7}  window")
    click.echo("-" * 96)
    for r in rows:
        window = f"{r['window_start']} → {r['window_end'] or 'today'}"
        click.echo(
            f"{r['run_at'][:19]:<21} {r['strategy_key']:<20} {r['hit_rate_pct']:>8.0f}% "
            f"{r['median_excess_pts']:>13.1f}p {r['total_trades']:>7}  {window}"
        )


@cli.group()
def portfolio():
    """Track deposits and score the account against the index.

    Every other number in this tool is about a strategy. These are about you:
    given the money actually deposited, on the dates it was deposited, is this
    account ahead of or behind simply buying the index with the same cash?
    That comparison cannot be fooled by a good week or a favourable window.
    """


@portfolio.command("contribute")
@click.option("--amount", type=float, required=True,
              help="Deposit (positive) or withdrawal (negative), in dollars.")
@click.option("--date", "on_date", default=None, help="YYYY-MM-DD (default: today).")
@click.option("--note", default=None)
@click.pass_obj
def portfolio_contribute(cfg, amount, on_date, note):
    """Log a deposit or withdrawal."""
    from datetime import date as _date

    from hf_trading_bot.portfolio import Contribution, ContributionLog, PortfolioError

    storage = _load_storage(cfg)
    log = ContributionLog(storage._conn)
    try:
        cid = log.add(Contribution(
            contributed_on=on_date or _date.today().isoformat(),
            amount=amount,
            note=note,
        ))
    except PortfolioError as e:
        storage.close()
        raise click.ClickException(str(e)) from e
    verb = "Deposit" if amount > 0 else "Withdrawal"
    click.echo(f"Recorded contribution #{cid}: {verb} ${abs(amount):,.2f}")
    storage.close()


@portfolio.command("list")
@click.pass_obj
def portfolio_list(cfg):
    """Show every recorded contribution."""
    from hf_trading_bot.portfolio import ContributionLog

    storage = _load_storage(cfg)
    log = ContributionLog(storage._conn)
    rows = log.rows()
    if not rows:
        click.echo("No contributions recorded yet.")
        storage.close()
        return
    click.echo(f"{'DATE':<12} {'AMOUNT':>12}  NOTE")
    for r in rows:
        click.echo(f"{r['contributed_on']:<12} {r['amount']:>12,.2f}  {r['note'] or ''}")
    click.echo(f"{'':<12} {sum(r['amount'] for r in rows):>12,.2f}  TOTAL")
    storage.close()


@portfolio.command("compare")
@click.option("--benchmark", default="SPY", help="Benchmark symbol (default SPY).")
@click.option("--value", type=float, default=None,
              help="Account value. Omit to read it live from the broker.")
@click.pass_obj
def portfolio_compare(cfg, benchmark, value):
    """Compare this account against the same cash flows put into the index."""
    from hf_trading_bot.portfolio import ContributionLog, PortfolioError, counterfactual

    storage = _load_storage(cfg)
    log = ContributionLog(storage._conn)
    contributions = log.all()
    if not contributions:
        storage.close()
        raise click.ClickException(
            "No contributions recorded. Log them with `hf-bot portfolio contribute` "
            "— without deposit dates there is nothing to compare against."
        )

    if value is None:
        try:
            broker = _build_broker(cfg)
            value = float(broker.get_account().equity)
        except Exception as e:
            storage.close()
            raise click.ClickException(
                f"Could not read account value from the broker ({e}). "
                f"Pass --value to supply it manually."
            ) from e

    provider = _build_provider(cfg)
    try:
        bars = provider.daily_bars_range(benchmark, start=log.first_date())
    except Exception as e:
        storage.close()
        raise click.ClickException(
            f"Could not fetch {benchmark} price history ({type(e).__name__}: {e}). "
            f"Check network access and data-source credentials, then retry."
        ) from e

    try:
        c = counterfactual(contributions, bars, actual_value=value, benchmark=benchmark)
    except PortfolioError as e:
        storage.close()
        raise click.ClickException(str(e)) from e
    storage.close()

    click.echo(f"\n  As of {c.as_of}   (contributions since {contributions[0].contributed_on})\n")
    click.echo(f"  {'Contributed':<22} ${c.total_contributed:>12,.2f}")
    click.echo(f"  {'This account':<22} ${c.actual_value:>12,.2f}   {c.actual_return_pct:>+7.1f}%")
    click.echo(f"  {'Same cash in ' + c.benchmark:<22} ${c.benchmark_value:>12,.2f}   "
               f"{c.benchmark_return_pct:>+7.1f}%")
    click.echo("  " + "-" * 48)
    click.echo(f"  {'Difference':<22} ${c.gap:>+12,.2f}   {c.excess_pct:>+7.1f} pts\n")

    if c.gap < 0:
        click.echo(
            f"  Behind {c.benchmark} by ${abs(c.gap):,.2f}. That is the real cost of\n"
            f"  active management here — a fee paid in performance, not in dollars.\n"
        )
    else:
        click.echo(
            f"  Ahead of {c.benchmark} by ${c.gap:,.2f}. Worth checking whether this\n"
            f"  came from skill or from one lucky position — the journal will say.\n"
        )


@cli.group()
def committee():
    """The investment committee's working record — who acted, and how work
    moved between agents during a review.

    Agents emit events here as they run (`committee log-event`), which is what
    makes the dashboard's activity view real: every handoff pulse and active
    node corresponds to a logged action, not an animation.
    """


_COMMITTEE_EVENT_TYPES = ["start", "handoff", "finding", "verdict", "memo"]


@committee.command("log-event")
@click.option("--run", "run_id", required=True, help="Run id grouping this review's events.")
@click.option("--agent", "agent_key", required=True, help="Acting agent (e.g. cio, red-team).")
@click.option("--type", "event_type", required=True,
              type=click.Choice(_COMMITTEE_EVENT_TYPES, case_sensitive=False))
@click.option("--summary", required=True, help="One line: what happened.")
@click.option("--symbol", default=None, help="Ticker under review (set on the run's first event).")
@click.option("--to", "to_agent", default=None, help="Handoff target, for --type handoff.")
@click.pass_obj
def committee_log_event(cfg, run_id, agent_key, event_type, summary, symbol, to_agent):
    """Record one agent action. Called by committee agents as they work."""
    storage = _load_storage(cfg)
    eid = storage.record_committee_event(
        run_id=run_id, agent_key=agent_key, event_type=event_type.lower(),
        summary=summary, symbol=symbol, to_agent=to_agent,
    )
    click.echo(f"logged event #{eid} [{run_id}] {agent_key} {event_type}"
               + (f" → {to_agent}" if to_agent else ""))
    storage.close()


@committee.command("runs")
@click.option("--limit", default=20)
@click.pass_obj
def committee_runs(cfg, limit):
    """List recent committee reviews, newest first."""
    storage = _load_storage(cfg)
    rows = storage.committee_run_ids(limit=limit)
    storage.close()
    if not rows:
        click.echo("No committee runs recorded yet.")
        return
    click.echo(f"{'run_id':<24} {'symbol':<8} {'events':>7} {'state':<9} last activity")
    click.echo("-" * 72)
    for r in rows:
        state = "complete" if r["concluded"] else "open"
        click.echo(f"{r['run_id']:<24} {r['symbol'] or '—':<8} {r['events']:>7} "
                   f"{state:<9} {r['last_at'][:19]}")


@committee.command("show")
@click.argument("run_id")
@click.pass_obj
def committee_show(cfg, run_id):
    """Replay one review's events in order."""
    storage = _load_storage(cfg)
    events = storage.committee_run_events(run_id)
    storage.close()
    if not events:
        click.echo(f"No events for run {run_id}.")
        return
    for e in events:
        arrow = f" → {e['to_agent']}" if e["to_agent"] else ""
        click.echo(f"  {e['seq']:>2}. {e['agent_key']:<18} {e['event_type']:<8}{arrow}")
        click.echo(f"      {e['summary']}")


@committee.command("demo")
@click.option("--symbol", default="ASTS", help="Ticker to stage a sample review for.")
@click.option("--partial", is_flag=True,
              help="Stop mid-review (no memo) so the dashboard shows an agent "
                   "actively working rather than a finished run.")
@click.pass_obj
def committee_demo(cfg, symbol, partial):
    """Record one realistic sample review, so the dashboard's activity view has
    something to render without waiting for a live committee run. The events are
    real DB rows following the actual pipeline — only the trigger is synthetic."""
    import datetime as _d

    storage = _load_storage(cfg)
    run_id = _d.datetime.now(_d.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-demo"
    steps = [
        ("cio", "start", None, f"opening committee review on {symbol}"),
        ("cio", "handoff", "macro-strategist", "requesting the current regime read"),
        ("macro-strategist", "finding", None, "late-cycle, liquidity tightening — favor quality, size small"),
        ("macro-strategist", "handoff", "equity-analyst", "regime noted; over to fundamentals"),
        ("equity-analyst", "finding", None, "spectrum + first-mover moat; cash burn is the risk"),
        ("equity-analyst", "handoff", "quant-analyst", "cross-check the setup statistically"),
        ("quant-analyst", "finding", None, "no tradable edge in the price series; thesis is fundamental, not technical"),
        ("quant-analyst", "handoff", "valuation-analyst", "over to valuation"),
        ("valuation-analyst", "finding", None, "reverse-DCF implies flawless execution; ~40% embedded upside if it lands"),
        ("valuation-analyst", "handoff", "red-team", "valuation done — attack it"),
        ("red-team", "verdict", None, "dilution before revenue is the kill case; survivable if sized small"),
        ("red-team", "handoff", "risk-manager", "not killed; size it"),
        ("risk-manager", "verdict", None, "cap at 4% of book, hard stop -30%"),
        ("risk-manager", "handoff", "behavioral-coach", "sizing set; gut-check the decision"),
        ("behavioral-coach", "verdict", None, "no FOMO signature; conviction is thesis-driven, proceed"),
        ("behavioral-coach", "handoff", "cio", "cleared all three gates"),
        ("cio", "memo", None, f"BUY {symbol}, 4% position, stop -30%, target +40% — asymmetric, sized for the risk"),
    ]
    # --partial cuts off after risk-manager hands to behavioral-coach, so the
    # coach shows as the agent currently working (no memo yet → state "active").
    if partial:
        steps = steps[:14]
    for agent_key, etype, to_agent, summary in steps:
        storage.record_committee_event(
            run_id=run_id, agent_key=agent_key, event_type=etype,
            summary=summary, symbol=symbol, to_agent=to_agent,
        )
    # A concluded review distills one durable memory episode — the recall
    # layer for the next review. (A partial run hasn't concluded, so none.)
    if not partial:
        storage.record_memory_episode(
            kind="decision", symbol=symbol, run_id=run_id,
            title=f"{symbol}: BUY — asymmetric payoff, sized small",
            body=(f"On {run_id[:8]}, the committee reviewed {symbol} and issued BUY at "
                  f"4% of book, stop -30%, target +40%. Red team's strongest objection: "
                  f"dilution before revenue — judged survivable because the position is "
                  f"sized small. Falsification: a rival reaches the same milestone first, "
                  f"or the runway assumption breaks. Sample/demo review."),
            mirrored_to_brain=False,
        )
    tail = " (partial — left mid-review)" if partial else ""
    click.echo(f"Recorded a {len(steps)}-event sample review as run {run_id}{tail}.")
    if not partial:
        click.echo("Distilled one shared-memory episode (`hf-bot memory list`).")
    click.echo("Open `hf-bot dashboard` to watch it, or `hf-bot committee show "
               f"{run_id}` to replay it in the terminal.")
    storage.close()


@cli.group()
def memory():
    """The committee's durable, collectively-shared memory.

    Distilled knowledge the team carries forward: what it concluded about a
    name, what it learned, what it rejected. Persisted in the repo's SQLite so
    it survives across sessions, and mirrored into the Agently knowledge graph
    (a cross-session / cross-tool brain) when that service is reachable. This
    is the recall layer — search it at the start of a review so the committee
    builds on past work instead of starting cold.
    """


_MEMORY_KINDS = ["decision", "finding", "lesson", "note"]


@memory.command("persist")
@click.option("--title", required=True, help="Short title.")
@click.option("--body", required=True, help="Self-contained text, with absolute dates.")
@click.option("--kind", default="note", type=click.Choice(_MEMORY_KINDS, case_sensitive=False))
@click.option("--symbol", default=None, help="Ticker this concerns, if any.")
@click.option("--run", "run_id", default=None, help="Committee run that produced it, if any.")
@click.option("--mirrored/--not-mirrored", default=False,
              help="Set --mirrored only after the episode was also written to Agently.")
@click.pass_obj
def memory_persist(cfg, title, body, kind, symbol, run_id, mirrored):
    """Append one durable episode to the shared memory ledger.

    Agents call this at the end of a review. Persisting here always works
    offline; mirroring to the Agently brain is a separate step the agent does
    when that service is available, then re-runs this with --mirrored.
    """
    storage = _load_storage(cfg)
    eid = storage.record_memory_episode(
        kind=kind.lower(), title=title, body=body, symbol=symbol,
        run_id=run_id, mirrored_to_brain=mirrored,
    )
    where = "shared brain + local ledger" if mirrored else "local ledger"
    click.echo(f"Stored memory episode #{eid} ({kind}) in the {where}.")
    storage.close()


@memory.command("list")
@click.option("--symbol", default=None, help="Filter to one ticker.")
@click.option("--limit", default=20)
@click.pass_obj
def memory_list(cfg, symbol, limit):
    """Show recent shared-memory episodes, newest first."""
    storage = _load_storage(cfg)
    eps = storage.recent_memory_episodes(limit=limit, symbol=symbol)
    storage.close()
    if not eps:
        click.echo("No memory episodes yet.")
        return
    for e in eps:
        mark = "◈" if e["mirrored_to_brain"] else "◇"
        sym = f"[{e['symbol']}] " if e["symbol"] else ""
        click.echo(f"  {mark} {e['created_at'][:10]}  {e['kind']:<9} {sym}{e['title']}")


@memory.command("recall")
@click.argument("query")
@click.option("--limit", default=10)
@click.pass_obj
def memory_recall(cfg, query, limit):
    """Search the shared-memory ledger — run this at the start of a review."""
    storage = _load_storage(cfg)
    eps = storage.search_memory_episodes(query, limit=limit)
    storage.close()
    if not eps:
        click.echo(f"Nothing in shared memory matches {query!r}.")
        return
    for e in eps:
        sym = f"[{e['symbol']}] " if e["symbol"] else ""
        click.echo(f"  {e['created_at'][:10]}  {sym}{e['title']}")
        click.echo(f"      {e['body']}")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address for the local server.")
@click.option("--port", default=8420, type=int, help="Port for the local server.")
@click.option("--refresh", default=15, type=int, help="Seconds between client polls.")
@click.option("--publish", "publish_path", default=None, type=click.Path(),
              help="Write a self-contained static HTML snapshot to this path instead "
                   "of serving live.")
@click.pass_obj
def dashboard(cfg: AppConfig, host: str, port: int, refresh: int, publish_path: Optional[str]):
    """Live Agent Cortex — a HUD visualization of the 11-agent committee.

    Each agent's firing-rate number is real logged data (journal, sweeps,
    watchlist), a disclosed proxy, or an honest 'no signal' — never a
    fabricated figure. It is a visualization of committee activity, not a
    trained model and not a price prediction.
    """
    import dataclasses
    import json

    from hf_trading_bot.cortex import build_snapshot
    from hf_trading_bot.cortex_render import render_html

    storage = _load_storage(cfg)

    if publish_path:
        snap = build_snapshot(storage)
        if snap.portfolio is None:
            click.echo(
                "  note: portfolio panel is empty (no contributions, no equity "
                "snapshot, or SPY bars unreachable) — LATTICE will show NO SIGNAL.",
                err=True,
            )
        html = render_html(snap, mode="static")
        with open(publish_path, "w") as f:
            f.write(html)
        click.echo(f"Wrote static cortex snapshot to {publish_path} "
                   f"({len(html):,} bytes, generated {snap.generated_at}).")
        storage.close()
        return

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    refresh_ms = max(2000, refresh * 1000)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            snap = build_snapshot(storage)
            if self.path.startswith("/api/snapshot.json"):
                body = json.dumps(dataclasses.asdict(snap)).encode()
                self._send(200, body, "application/json")
                return
            html = render_html(snap, mode="live")
            html = html.replace(
                "window.__CORTEX__ =",
                f"window.__CORTEX_REFRESH_MS__ = {refresh_ms};\nwindow.__CORTEX__ =",
                1,
            )
            self._send(200, html.encode(), "text/html; charset=utf-8")

        def log_message(self, *args):
            pass  # silence default stderr access logging

    server = ThreadingHTTPServer((host, port), Handler)
    click.echo(f"Live Agent Cortex — http://{host}:{port}  (refresh {refresh}s, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        server.server_close()
        storage.close()


if __name__ == "__main__":
    cli()
