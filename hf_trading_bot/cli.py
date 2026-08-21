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


@committee.command("enqueue")
@click.argument("text")
@click.pass_obj
def committee_enqueue(cfg, text):
    """Queue a review command (as the dashboard's command deck does).

    TEXT is free-form like 'review ASTS' or just 'ASTS'; only a validated
    ticker is ever stored. Nothing runs — a Claude executor picks it up.
    """
    from hf_trading_bot.cortex import CommandError, parse_review_command, review_prompt

    storage = _load_storage(cfg)
    try:
        symbol = parse_review_command(text)
    except CommandError as e:
        storage.close()
        raise click.ClickException(str(e)) from e
    cid = storage.enqueue_command("review", review_prompt(symbol), symbol=symbol)
    click.echo(f"Queued command #{cid}: review {symbol} (status: pending).")
    click.echo("An executor runs it with `hf-bot committee queue` — see that command's help.")
    storage.close()


@committee.command("archive")
@click.option("--keep", default=3, type=int,
              help="How many of the newest responses to leave in the console.")
@click.pass_obj
def committee_archive(cfg, keep):
    """Move old committee responses out of the dashboard into clean Markdown
    files under research/committee/ (all but the newest --keep). The dashboard
    then shows only recent exchanges; nothing is lost."""
    from hf_trading_bot import committee_archive as arch

    storage = _load_storage(cfg)
    try:
        written = arch.archive_commands(storage, keep=keep)
    finally:
        storage.close()
    if not written:
        click.echo("Nothing to archive — the console is already clean.")
        return
    click.echo(f"Archived {len(written)} response(s) to {arch.ARCHIVE_DIR}/ :")
    for p in written[-10:]:
        click.echo(f"  {p}")
    if len(written) > 10:
        click.echo(f"  … and {len(written) - 10} more.")


@committee.command("queue")
@click.option("--run", "run_next", is_flag=True,
              help="Print the next pending command's prompt for an executor to run.")
@click.pass_obj
def committee_queue(cfg, run_next):
    """Show queued review commands awaiting an executor.

    THE EXECUTOR IS A CLAUDE SESSION. The Python CLI cannot run the committee
    agents itself. To process the queue from Claude Code:

      1. `hf-bot committee queue --run` prints the next command's instruction.
      2. Run that instruction (it invokes the cio agent, which emits the
         events that light up the dashboard).
      3. `hf-bot committee resolve <id> --status done` when it finishes.
    """
    storage = _load_storage(cfg)
    pending = storage.pending_commands()
    if run_next:
        if not pending:
            click.echo("# no pending commands")
        else:
            c = pending[0]
            click.echo(f"# command #{c['id']} — mark done with: hf-bot committee resolve {c['id']} --status done")
            click.echo(c["prompt"])
        storage.close()
        return
    if not pending:
        click.echo("No pending commands.")
    else:
        click.echo(f"{len(pending)} pending:")
        for c in pending:
            click.echo(f"  #{c['id']:<4} {c['symbol'] or '—':<8} queued {c['created_at'][:19]}")
    storage.close()


@committee.command("resolve")
@click.argument("command_id", type=int)
@click.option("--status", required=True,
              type=click.Choice(["running", "done", "failed", "unavailable"]))
@click.option("--detail", default=None, help="One-line result or error.")
@click.option("--run", "run_id", default=None, help="Committee run id this produced.")
@click.pass_obj
def committee_resolve(cfg, command_id, status, detail, run_id):
    """Mark a queued command's status (the executor calls this)."""
    storage = _load_storage(cfg)
    if storage.get_command(command_id) is None:
        storage.close()
        raise click.ClickException(f"No command #{command_id}.")
    storage.update_command(command_id, status=status, detail=detail, run_id=run_id)
    click.echo(f"Command #{command_id} → {status}.")
    storage.close()


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


@cli.group()
def study():
    """The team's continuous learning — each agent building a role-specific
    library it recalls at task time.

    The curriculum (knowledge/curriculum.yaml) lists what each agent should
    study and why. The Python CLI cannot research — only a Claude session can —
    so `study next` prints a brief for an executor to run; the executor writes
    the distilled note under knowledge/<agent>/ and calls `study record`.
    """


@study.command("status")
@click.pass_obj
def study_status(cfg):
    """Per-agent library size — the team's accumulated expertise."""
    from hf_trading_bot.curriculum import agent_keys, coverage

    storage = _load_storage(cfg)
    keys = agent_keys()
    if not keys:
        click.echo("No curriculum found (knowledge/curriculum.yaml).")
        storage.close()
        return
    absorbed_total = 0
    topic_total = 0
    click.echo(f"{'agent':<20} {'absorbed':>10}   library")
    click.echo("-" * 52)
    for k in keys:
        studied = storage.studied_topics(k)
        a, t = coverage(k, studied)
        absorbed_total += a
        topic_total += t
        bar = "█" * a + "·" * (t - a)
        click.echo(f"{k:<20} {f'{a}/{t}':>10}   {bar}")
    click.echo("-" * 52)
    click.echo(f"{'TOTAL':<20} {f'{absorbed_total}/{topic_total}':>10}")
    storage.close()


@study.command("next")
@click.option("--agent", "agent_key", default=None,
              help="Study the next topic for this agent. Omit to pick the least-studied agent.")
@click.pass_obj
def study_next(cfg, agent_key):
    """Print the next uncovered topic and a study brief for a Claude executor."""
    from hf_trading_bot.curriculum import agent_keys, coverage, next_topic, study_brief

    storage = _load_storage(cfg)
    keys = agent_keys()
    if not keys:
        storage.close()
        raise click.ClickException("No curriculum found (knowledge/curriculum.yaml).")
    if agent_key is None:
        # pick the agent with the lowest coverage ratio, then most-behind first
        def behind(k):
            a, t = coverage(k, storage.studied_topics(k))
            return (a / t if t else 1.0, a)
        agent_key = min(keys, key=behind)
    elif agent_key not in keys:
        storage.close()
        raise click.ClickException(f"Unknown agent {agent_key!r}. Known: {', '.join(keys)}")

    t = next_topic(agent_key, storage.studied_topics(agent_key))
    storage.close()
    if t is None:
        click.echo(f"# {agent_key} has absorbed its whole curriculum. Nothing to study.")
        return
    click.echo(study_brief(t))


@study.command("record")
@click.option("--agent", "agent_key", required=True)
@click.option("--topic", required=True, help="Curriculum topic id that was studied.")
@click.option("--slug", required=True, help="knowledge/<agent>/<slug>.md that was written.")
@click.option("--sources", "sources_count", type=int, default=0)
@click.pass_obj
def study_record(cfg, agent_key, topic, slug, sources_count):
    """Mark a topic absorbed (the executor calls this after writing the note)."""
    from hf_trading_bot.curriculum import agent_keys

    storage = _load_storage(cfg)
    if agent_key not in agent_keys():
        storage.close()
        raise click.ClickException(f"Unknown agent {agent_key!r}.")
    storage.record_study(agent_key, topic, slug, sources_count=sources_count)
    click.echo(f"Recorded: {agent_key} studied '{topic}' "
               f"(knowledge/{agent_key}/{slug}.md, {sources_count} sources).")
    storage.close()


@study.command("cycle")
@click.option("--rounds", default=1, help="How many agents to dispatch this cycle.")
@click.pass_obj
def study_cycle(cfg, rounds):
    """Print briefs for the next N least-studied agents — one study cycle.

    A Claude session runs these; each dispatched agent researches its topic,
    writes the note, and calls `study record`. This is the 'trickle' entry
    point; a scheduled Routine can call it (see the README).
    """
    from hf_trading_bot.curriculum import agent_keys, coverage, next_topic, study_brief

    storage = _load_storage(cfg)
    keys = agent_keys()
    if not keys:
        storage.close()
        raise click.ClickException("No curriculum found (knowledge/curriculum.yaml).")

    def behind(k):
        a, t = coverage(k, storage.studied_topics(k))
        return (a / t if t else 1.0, a)

    ordered = sorted(keys, key=behind)
    dispatched = 0
    for k in ordered:
        if dispatched >= rounds:
            break
        t = next_topic(k, storage.studied_topics(k))
        if t is None:
            continue
        click.echo(f"\n{'=' * 70}\n# CYCLE {dispatched + 1}/{rounds} — {k}\n{'=' * 70}")
        click.echo(study_brief(t))
        dispatched += 1
    storage.close()
    if dispatched == 0:
        click.echo("Every agent has absorbed its whole curriculum. Nothing to study.")


@cli.group()
def models():
    """Tiered model routing — run cheap work on cheap models, save Claude for
    the hard calls.

    Configure providers in .env (git-ignored):
        NVIDIA_API_KEY / NVIDIA_BASE_URL / NVIDIA_MODEL   -> cheap (research)
        OLLAMA_API_KEY / OLLAMA_BASE_URL / OLLAMA_MODEL   -> mid (screening)
    'top' is Claude and is served by the committee itself.
    """


@models.command("check")
@click.option("--ping/--no-ping", default=True,
              help="Actually call each configured tier to confirm it answers.")
def models_check(ping):
    """Show which tiers are configured, and (default) ping them."""
    from hf_trading_bot import model_router

    avail = model_router.available()
    providers = model_router.load_providers()
    any_ok = False
    for tier in ("cheap", "mid"):
        p = providers[tier]
        if not avail[tier]:
            click.echo(f"  {tier:5} [{p.name}]  not configured "
                       f"(set its *_API_KEY/*_MODEL in .env)")
            continue
        any_ok = True
        line = f"  {tier:5} [{p.name}]  {p.model}  @ {p.base_url}"
        if ping:
            try:
                reply = model_router.ping(tier)
                line += f"  -> OK ({reply[:20]!r})"
            except model_router.RouterError as e:
                line += f"  -> FAILED: {str(e)[:120]}"
        click.echo(line)
    click.echo("  top   [claude]  served by the committee (the `claude` executor)")
    if not any_ok:
        click.echo("\nNo cheap/mid tiers configured yet — add keys to .env. "
                   "See .env.example.")


@models.command("list")
@click.option("--tier", default="cheap", type=click.Choice(["cheap", "mid"]),
              help="Which provider to list models for.")
@click.option("--filter", "needle", default=None,
              help="Only show model ids containing this substring (e.g. 'nemotron').")
def models_list(tier, needle):
    """List the model ids your key can actually call — copy one into
    NVIDIA_MODEL / OLLAMA_MODEL to fix a 404 'model not found'."""
    from hf_trading_bot import model_router

    try:
        ids = model_router.list_models(tier)
    except model_router.RouterError as e:
        raise click.ClickException(f"Could not list models for {tier!r}: {e}")
    if needle:
        ids = [m for m in ids if needle.lower() in m.lower()]
    if not ids:
        click.echo("No models returned"
                   + (f" matching {needle!r}." if needle else "."))
        return
    click.echo(f"{len(ids)} model(s) your key can call"
               + (f" matching {needle!r}" if needle else "") + ":")
    for m in ids:
        click.echo(f"  {m}")


@models.command("route")
@click.argument("text")
@click.option("--kind", default="console",
              help="Work kind: console (default) or study.")
def models_route(text, kind):
    """Show which tier a piece of work would route to, and why."""
    from hf_trading_bot import model_router

    tier = model_router.classify(kind, text)
    where = {"cheap": "NVIDIA (research model)", "mid": "Ollama (screening model)",
             "top": "Claude (the full committee)"}[tier]
    click.echo(f"kind={kind!r}  ->  tier={tier!r}  ->  {where}")


@models.command("study")
@click.option("--agent", "agent_key", default=None,
              help="Which agent studies. Omit to pick the least-covered agent.")
@click.option("--tier", default="cheap", type=click.Choice(["cheap", "mid"]),
              help="Which non-Claude tier writes the note.")
@click.pass_obj
def models_study(cfg, agent_key, tier):
    """Study a topic on a cheap model and PERSIST it — grows the library with
    no Claude tokens. Writes knowledge/<agent>/<slug>.md and records it."""
    from hf_trading_bot import model_router, study_runner
    from hf_trading_bot.curriculum import agent_keys, coverage, next_topic

    storage = _load_storage(cfg)
    keys = agent_keys()
    if not keys:
        storage.close()
        raise click.ClickException("No curriculum found (knowledge/curriculum.yaml).")
    if not model_router.available().get(tier):
        storage.close()
        raise click.ClickException(
            f"Tier {tier!r} isn't configured. Add its keys to .env "
            f"(see `hf-bot models check`).")
    if agent_key is None:
        def behind(k):
            a, t = coverage(k, storage.studied_topics(k))
            return (a / t if t else 1.0, a)
        agent_key = min((k for k in keys
                         if next_topic(k, storage.studied_topics(k))),
                        key=behind, default=None)
        if agent_key is None:
            storage.close()
            click.echo("Every agent has absorbed its whole curriculum. Nothing to study.")
            return
    try:
        result = study_runner.study_one(agent_key, storage, tier=tier)
    except model_router.RouterError as e:
        storage.close()
        raise click.ClickException(f"Study failed on tier {tier!r}: {e}")
    storage.close()
    if not result.get("studied"):
        click.echo(f"{agent_key}: {result.get('reason', 'nothing to study')}.")
        return
    click.echo(f"Studied: {result['agent']} — '{result['topic']}' "
               f"via {result['provider']} ({result['chars']} chars)\n"
               f"  wrote {result['path']} and recorded it. Library +1.")


@models.command("study-cycle")
@click.option("--rounds", default=3, help="How many agents to study this cycle.")
@click.option("--tier", default="cheap", type=click.Choice(["cheap", "mid"]))
@click.pass_obj
def models_study_cycle(cfg, rounds, tier):
    """Study the next topic for the N least-covered agents on a cheap model,
    persisting each. The free 'trickle' — no Claude tokens."""
    from hf_trading_bot import model_router, study_runner
    from hf_trading_bot.curriculum import agent_keys, coverage, next_topic

    storage = _load_storage(cfg)
    keys = agent_keys()
    if not keys:
        storage.close()
        raise click.ClickException("No curriculum found (knowledge/curriculum.yaml).")
    if not model_router.available().get(tier):
        storage.close()
        raise click.ClickException(
            f"Tier {tier!r} isn't configured. Add its keys to .env.")

    def behind(k):
        a, t = coverage(k, storage.studied_topics(k))
        return (a / t if t else 1.0, a)

    ordered = [k for k in sorted(keys, key=behind)
               if next_topic(k, storage.studied_topics(k))]
    done = 0
    for k in ordered:
        if done >= rounds:
            break
        try:
            result = study_runner.study_one(k, storage, tier=tier)
        except model_router.RouterError as e:
            click.echo(f"  {k}: FAILED ({str(e)[:100]}) — stopping cycle.")
            break
        if result.get("studied"):
            click.echo(f"  {k}: studied '{result['topic']}' (+1)")
            done += 1
    storage.close()
    click.echo(f"\nStudy cycle done — {done} topic(s) added via tier {tier!r}, "
               f"0 Claude tokens.")


_SPARK = "▁▂▃▄▅▆▇█"


@cli.command("chart")
@click.argument("symbol")
@click.option("--days", default=60, help="Lookback in daily bars.")
@click.pass_obj
def chart_cmd(cfg, symbol, days):
    """Price + a terminal chart from Alpaca market data (yfinance fallback).

    Quick read for SNIPER or you: last price, change, range, and a sparkline
    of recent closes."""
    symbol = symbol.upper()
    provider = _build_provider(cfg)
    try:
        bars = (provider.daily_bars([symbol], lookback_days=days) or {}).get(symbol) or []
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"Could not fetch {symbol} bars: {e}") from e
    if len(bars) < 2:
        raise click.ClickException(f"No usable price history for {symbol}.")

    closes = [b.c for b in bars]
    last, prev = closes[-1], closes[-2]
    chg = (last / prev - 1) * 100
    hi, lo = max(b.h for b in bars), min(b.l for b in bars)
    span = (hi - lo) or 1
    spark = "".join(_SPARK[min(7, int((c - lo) / span * 7.999))] for c in closes[-48:])

    from hf_trading_bot.data.provider import source_of
    click.echo(f"\n  {symbol}   ${last:,.2f}   {chg:+.2f}% (1d)      source: {source_of(provider)}")
    click.echo(f"  {days}-day range  ${lo:,.2f} — ${hi:,.2f}")
    click.echo(f"  {spark}")
    click.echo(f"  {bars[0].t[:10]} → {bars[-1].t[:10]}\n")


@cli.command("seed-demo")
@click.pass_obj
def seed_demo(cfg):
    """Populate sample data so every agent lights up on the dashboard.

    Clearly-labeled DEMO data — delete the DB (or use a fresh --config) to
    reset. Uses no network."""
    import datetime as _d

    from hf_trading_bot.journal import Decision, Journal
    from hf_trading_bot.portfolio import Contribution, ContributionLog

    storage = _load_storage(cfg)
    j = Journal(storage._conn)
    j.record(Decision(symbol="ASTS", decision="BUY", conviction="high",
        thesis="Direct-to-cell has a multi-year lead and a spectrum moat (demo data).",
        falsification="A rival reaches commercial direct-to-cell first, or launch cadence slips past 2027.",
        red_team_objection="Cash burn forces dilution before revenue.",
        entry_price=28, stop_price=19, target_price=55, position_pct=4))
    j.record(Decision(symbol="NVDA", decision="BUY", conviction="medium",
        thesis="Datacenter GPU demand is structurally underestimated (demo data).",
        falsification="Two consecutive quarters of falling hyperscaler capex, or gross margin below 60%.",
        red_team_objection="Custom silicon erodes the CUDA moat.",
        entry_price=170, stop_price=140, target_price=240, position_pct=6))
    did = j.record(Decision(symbol="PLTR", decision="BUY", conviction="medium",
        thesis="Government AI backlog is compounding and commercial is inflecting (demo data).",
        falsification="Commercial net-retention below 115%, or a major government cancellation.",
        red_team_objection="Valuation leaves no margin of safety.",
        entry_price=25, stop_price=20, target_price=40, position_pct=3))
    j.review(did, outcome="right", exit_price=38, pnl_pct=52, followed_own_rules=True,
             lessons="Exited at the pre-committed target (demo).")
    j.record(Decision(symbol="INTC", decision="PASS", conviction="low",
        thesis="No durable moat at this price (demo data).",
        falsification="Foundry execution turns and yields recover."))

    storage.record_sweep_result(run_id="demo", strategy_key="rsi_mean_reversion",
        symbols_tested=24, wins=8, hit_rate_pct=33.0, median_excess_pts=-37.4,
        total_trades=185, window_start="2021-01-01", window_end="2026-08-15")

    for i, (sym, strat) in enumerate([("AAPL", "momentum_90d"), ("NVDA", "momentum_90d"),
                                      ("MSFT", "sma_crossover")]):
        storage.upsert_watchlist_symbol(sym, strat, live_enabled=True, rank=i)

    clog = ContributionLog(storage._conn)
    clog.add(Contribution("2026-06-01", 5000.0, "demo"))
    clog.add(Contribution("2026-07-01", 2000.0, "demo"))
    eq = 7000.0
    for i in range(12):
        eq *= 1 + (0.011 if i % 3 else -0.006)
        d = (_d.date(2026, 7, 1) + _d.timedelta(days=i * 3)).isoformat()
        storage._conn.execute(
            "INSERT INTO equity_snapshots (equity, cash, snapshot_at) VALUES (?,?,?)",
            (round(eq, 2), round(eq * 0.4, 2), d + "T16:00:00+00:00"))
    storage._conn.commit()

    rid = "demo-" + _d.datetime.now(_d.timezone.utc).strftime("%H%M%S")
    steps = [("cio", "start", None, "opening demo review on ASTS"),
             ("cio", "handoff", "macro-strategist", "requesting the regime read"),
             ("macro-strategist", "finding", None, "late-cycle, tightening — size small"),
             ("macro-strategist", "handoff", "equity-analyst", "over to fundamentals"),
             ("equity-analyst", "finding", None, "spectrum moat intact, cash burn is the risk"),
             ("equity-analyst", "handoff", "sniper", "time the entry"),
             ("sniper", "finding", None, "basing above support, volume firming"),
             ("sniper", "handoff", "red-team", "attack it"),
             ("red-team", "verdict", None, "dilution is the kill case; survivable if small"),
             ("red-team", "handoff", "risk-manager", "size it"),
             ("risk-manager", "verdict", None, "cap 4%, stop -30%"),
             ("risk-manager", "handoff", "cio", "cleared the gates"),
             ("cio", "memo", None, "BUY ASTS, 4% (demo)")]
    for a, t, to, summ in steps:
        storage.record_committee_event(rid, a, t, summ, symbol="ASTS", to_agent=to)

    storage.record_memory_episode(kind="lesson",
        title="Blow-ups share a signature: leverage + hidden correlation (demo)",
        body="LTCM/Enron/Archegos — leverage + illiquidity + a correlation treated as independent.")
    for a, topic in [("red-team", "famous-blowups"),
                     ("equity-analyst", "value-investing-foundations"),
                     ("sniper", "price-action-and-candles")]:
        storage.record_study(a, topic, topic, sources_count=3)

    storage.record_order_proposal(symbol="ASTS", side="buy", qty=7.14, est_price=28.0,
        est_notional=200.0, stop_price=19.0, broker=cfg.broker, decision_id=1,
        rationale="demo — approve from the Orders panel")

    click.echo("Seeded DEMO data. Launch `hf-bot dashboard --open` — every agent should light up.")
    click.echo("This is labeled demo data; delete the DB or use a fresh --config to reset.")
    storage.close()


@cli.command("account")
@click.pass_obj
def account_cmd(cfg):
    """Show the broker balance and record an equity snapshot (feeds the chart)."""
    storage = _load_storage(cfg)
    try:
        broker = _build_broker(cfg)
        acct = broker.get_account()
    except Exception as e:  # noqa: BLE001
        storage.close()
        raise click.ClickException(f"Could not read the broker: {e}") from e
    storage.record_equity_snapshot(equity=acct.equity, cash=acct.cash)
    chg = (acct.equity - acct.last_equity)
    click.echo(f"  Equity        ${acct.equity:>12,.2f}   ({chg:+,.2f} vs last)")
    click.echo(f"  Cash          ${acct.cash:>12,.2f}")
    click.echo(f"  Buying power  ${acct.buying_power:>12,.2f}   (broker: {cfg.broker})")
    click.echo("  Snapshot recorded — it will show on the dashboard's Account chart.")
    storage.close()


@cli.group()
def order():
    """The execution bridge — turn a committee decision into a broker order.

    A decision becomes a PROPOSED order (sized against your risk limits, nothing
    sent). You approve it deliberately; only then is it placed — paper money
    only, behind the kill switch and position caps. Nothing here auto-trades.
    """


def _price_for(broker, symbol: str) -> float:
    bars = broker.get_daily_bars([symbol])
    series = bars.get(symbol) or bars.get(symbol.upper()) or []
    if not series:
        raise click.ClickException(
            f"No price available for {symbol} — check the symbol and your data source."
        )
    return series[-1].c


@order.command("propose")
@click.option("--symbol", default=None)
@click.option("--side", type=click.Choice(["buy", "sell"], case_sensitive=False), default=None)
@click.option("--pct", "position_pct", type=float, default=None,
              help="Target size as % of equity (BUY). Omit for the small default.")
@click.option("--stop", "stop_price", type=float, default=None)
@click.option("--target", "take_profit", type=float, default=None)
@click.option("--decision", "decision_id", type=int, default=None,
              help="Journal decision id — auto-fills symbol/side/size/stop from the decision.")
@click.option("--rationale", default=None)
@click.pass_obj
def order_propose(cfg, symbol, side, position_pct, stop_price, take_profit, decision_id, rationale):
    """Size and record a PROPOSED order. Does not place anything.

    Give --decision <id> to pull symbol, side, size, and stop straight from a
    committee journal decision (explicit flags still override)."""
    from hf_trading_bot.execution import ExecutionError, build_proposal
    from hf_trading_bot.journal import Journal

    storage = _load_storage(cfg)
    if decision_id is not None:
        d = Journal(storage._conn).get(decision_id)
        if not d:
            storage.close()
            raise click.ClickException(f"No journal decision #{decision_id}.")
        symbol = symbol or d["symbol"]
        side = side or {"BUY": "buy", "SELL": "sell"}.get((d["decision"] or "").upper())
        position_pct = position_pct if position_pct is not None else d["position_pct"]
        stop_price = stop_price if stop_price is not None else d["stop_price"]
        take_profit = take_profit if take_profit is not None else d["target_price"]
        rationale = rationale or f"decision #{decision_id}: {d['decision']} {d['symbol']}"
    if not symbol or not side:
        storage.close()
        raise click.ClickException(
            "Need --symbol and --side (or a --decision that supplies them)."
        )

    broker = _build_broker(cfg)
    settings = storage.get_settings()
    try:
        price = _price_for(broker, symbol.upper())
        acct = broker.get_account()
        held_qty = held_value = 0.0
        for p in broker.get_positions():
            if p.symbol.upper() == symbol.upper():
                held_qty, held_value = p.qty, p.market_value
        proposal = build_proposal(
            symbol=symbol, side=side, price=price, equity=acct.equity,
            buying_power=acct.buying_power, max_position_pct=settings["max_position_pct"],
            position_pct=position_pct, held_qty=held_qty, held_value=held_value,
            stop_price=stop_price, take_profit=take_profit,
            decision_id=decision_id, rationale=rationale or "",
        )
    except ExecutionError as e:
        storage.close()
        raise click.ClickException(str(e)) from e

    pid = storage.record_order_proposal(
        symbol=proposal.symbol, side=proposal.side, qty=proposal.qty,
        est_price=proposal.est_price, est_notional=proposal.est_notional,
        stop_price=proposal.stop_price, take_profit=proposal.take_profit,
        rationale=proposal.rationale, broker=cfg.broker, decision_id=decision_id,
    )
    click.echo(f"Proposed order #{pid}: {proposal.summary()}")
    click.echo(f"Approve it with:  hf-bot order approve {pid}   (or reject {pid})")
    storage.close()


@order.command("list")
@click.option("--limit", default=15)
@click.pass_obj
def order_list(cfg, limit):
    """Recent order proposals and their status."""
    storage = _load_storage(cfg)
    rows = storage.recent_order_proposals(limit=limit)
    storage.close()
    if not rows:
        click.echo("No order proposals yet.")
        return
    for r in rows:
        click.echo(f"  #{r['id']:<4} {r['status']:<9} {r['side'].upper():<4} "
                   f"{r['qty']:.4f} {r['symbol']:<6} ~${r['est_notional']:,.2f}"
                   + (f"  [{r['broker_order_id']}]" if r['broker_order_id'] else "")
                   + (f"  {r['detail']}" if r['detail'] and r['status'] in ('failed', 'rejected') else ""))


@order.command("show")
@click.argument("proposal_id", type=int)
@click.pass_obj
def order_show(cfg, proposal_id):
    """Full detail of one proposal."""
    storage = _load_storage(cfg)
    r = storage.get_order_proposal(proposal_id)
    storage.close()
    if not r:
        raise click.ClickException(f"No proposal #{proposal_id}.")
    for k, v in r.items():
        click.echo(f"  {k:<16} {v}")


@order.command("approve")
@click.argument("proposal_id", type=int)
@click.pass_obj
def order_approve(cfg, proposal_id):
    """Validate and PLACE a proposed order (paper only, behind every guard)."""
    from hf_trading_bot.execution import ProposedOrder, place, validate

    storage = _load_storage(cfg)
    r = storage.get_order_proposal(proposal_id)
    if not r:
        storage.close()
        raise click.ClickException(f"No proposal #{proposal_id}.")
    if r["status"] != "proposed":
        storage.close()
        raise click.ClickException(
            f"Proposal #{proposal_id} is '{r['status']}', not 'proposed' — nothing to approve."
        )

    broker = _build_broker(cfg)
    settings = storage.get_settings()
    proposal = ProposedOrder(
        symbol=r["symbol"], side=r["side"], qty=r["qty"], est_price=r["est_price"],
        est_notional=r["est_notional"], stop_price=r["stop_price"],
        take_profit=r["take_profit"], decision_id=r["decision_id"],
    )
    # Re-check against live state at approval time — the account and the kill
    # switch may have changed since the proposal was written.
    try:
        _price_for(broker, proposal.symbol)   # ensure the broker has a price
        acct = broker.get_account()
    except click.ClickException:
        raise
    except Exception as e:  # noqa: BLE001
        storage.close()
        raise click.ClickException(f"Could not read the broker: {e}") from e

    ok, reasons = validate(
        proposal, broker_name=cfg.broker,
        kill_switch=bool(settings["kill_switch_active"]),
        buying_power=acct.buying_power,
    )
    if not ok:
        storage.update_order_proposal(proposal_id, status="rejected",
                                      detail="; ".join(reasons))
        storage.close()
        raise click.ClickException("Refused:\n  - " + "\n  - ".join(reasons))

    try:
        placed = place(broker, proposal)
    except Exception as e:  # noqa: BLE001
        storage.update_order_proposal(proposal_id, status="failed", detail=str(e))
        storage.close()
        raise click.ClickException(f"Order placement failed: {e}") from e

    storage.update_order_proposal(
        proposal_id, status=placed.status or "placed",
        detail=f"placed via {cfg.broker}", broker_order_id=placed.id,
    )
    click.echo(f"PLACED #{proposal_id}: {proposal.summary()}  →  order {placed.id} ({placed.status})")
    storage.close()


@order.command("reject")
@click.argument("proposal_id", type=int)
@click.pass_obj
def order_reject(cfg, proposal_id):
    """Reject a proposed order so it never gets placed."""
    storage = _load_storage(cfg)
    r = storage.get_order_proposal(proposal_id)
    if not r:
        storage.close()
        raise click.ClickException(f"No proposal #{proposal_id}.")
    storage.update_order_proposal(proposal_id, status="rejected", detail="rejected by principal")
    click.echo(f"Rejected proposal #{proposal_id}.")
    storage.close()


@cli.group()
def memecoin():
    """Solana / pump.fun-style memecoin trading — real money, no paper mode.

    Separate from everything else in this bot: it is never reachable from the
    committee, --auto-execute, or any scheduled job. Every trade here is a
    direct command you typed, gated by HF_BOT_I_UNDERSTAND_MEMECOIN_RISK, the
    kill switch, a per-trade ceiling, and a cumulative wallet budget. See the
    README's "Memecoin trading" section before using this — most tokens on
    this market are designed to be dumped on buyers, and there is no undo on
    a broadcast transaction.
    """


@memecoin.command("wallet")
@click.pass_obj
def memecoin_wallet(cfg):
    """Show the configured wallet's address, SOL balance, and budget used."""
    from hf_trading_bot import memecoin, solana_wallet

    storage = _load_storage(cfg)
    try:
        keypair = solana_wallet.load_keypair()
        pub = solana_wallet.pubkey_str(keypair)
        sol = solana_wallet.get_balance_sol(pub)
        net = storage.memecoin_net_deployed_usd()
        budget = memecoin.budget_usd()
        click.echo(f"Wallet:  {pub}")
        click.echo(f"Balance: {sol:.4f} SOL")
        click.echo(f"Budget:  ${net:,.2f} / ${budget:,.2f} deployed "
                   f"(${max(0.0, budget - net):,.2f} remaining)")
        click.echo(f"Confirm flag: {'set' if memecoin.is_confirmed() else 'NOT SET — trades will be refused'}")
    except (solana_wallet.WalletError, memecoin.MemecoinError) as e:
        raise click.ClickException(str(e))
    finally:
        storage.close()


@memecoin.command("scan")
@click.option("--query", default=None, help="Search by symbol/name/mint address instead of trending.")
@click.option("--limit", default=15, type=int)
def memecoin_scan(query, limit):
    """List trending Solana tokens, or search for one. Data only — no wallet
    touched, nothing spent. Attention is not a recommendation."""
    from hf_trading_bot import memecoin_data

    try:
        rows = (memecoin_data.search(query)[:limit] if query
               else memecoin_data.trending(limit=limit))
    except memecoin_data.DexScreenerError as e:
        raise click.ClickException(str(e))
    if not rows:
        click.echo("No results.")
        return
    for t in rows:
        click.echo(f"{(t['symbol'] or '?'):<10} {t['address']}\n"
                   f"  price ${t['price_usd']:.8f}  liq ${t['liquidity_usd']:,.0f}  "
                   f"vol24h ${t['volume_24h_usd']:,.0f}"
                   if t['price_usd'] is not None else
                   f"{(t['symbol'] or '?'):<10} {t['address']}  (no price data)")


@memecoin.command("quote")
@click.option("--token", "token_address", required=True, help="Token mint address to buy.")
@click.option("--usd", "usd_amount", required=True, type=float)
def memecoin_quote_cmd(token_address, usd_amount):
    """Preview a buy — expected tokens out and price impact. Spends nothing;
    safe to run regardless of guards."""
    from hf_trading_bot import memecoin

    try:
        p = memecoin.preview_buy(token_address, usd_amount)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(str(e))
    click.echo(f"${usd_amount:,.2f}  ->  ~{p.sol_amount:.5f} SOL  ->  "
              f"{int(p.quote.get('outAmount') or 0):,} raw units of {token_address}")
    click.echo(f"Price impact: {p.price_impact_pct:.2f}%")


@memecoin.command("buy")
@click.option("--token", "token_address", required=True, help="Token mint address to buy.")
@click.option("--usd", "usd_amount", required=True, type=float)
@click.option("--slippage-bps", default=100, type=int, help="Max slippage, in basis points.")
@click.option("--dry-run", is_flag=True, help="Preview only — sign and send nothing.")
@click.pass_obj
def memecoin_buy(cfg, token_address, usd_amount, slippage_bps, dry_run):
    """Buy TOKEN with USD_AMOUNT worth of SOL. Real money — see `hf-bot
    memecoin wallet` and the README before using this."""
    from hf_trading_bot import memecoin

    storage = _load_storage(cfg)
    try:
        settings = storage.get_settings()
        result = memecoin.execute_buy(
            token_address, usd_amount, storage,
            kill_switch=bool(settings["kill_switch_active"]),
            slippage_bps=slippage_bps, dry_run=dry_run)
    except memecoin.MemecoinError as e:
        storage.close()
        raise click.ClickException(str(e))
    finally:
        storage.close()
    if result.get("dry_run"):
        click.echo(f"DRY RUN — would spend {result['sol_amount']:.5f} SOL (${usd_amount:,.2f}), "
                   f"price impact {result['price_impact_pct']:.2f}%. Nothing signed or sent.")
        return
    click.echo(f"BUY submitted: {result['sol_amount']:.5f} SOL -> {token_address}")
    click.echo(f"  tx {result['tx_signature']}  status: {result['status']}")
    try:
        from hf_trading_bot import notify
        notify.notify(f"VANTRIX memecoin BUY — {token_address[:8]}…",
                      f"${usd_amount:,.2f} -> {result['sol_amount']:.5f} SOL spent\n"
                      f"tx {result['tx_signature']}  status {result['status']}")
    except Exception:  # noqa: BLE001
        pass


@memecoin.command("sell")
@click.option("--token", "token_address", required=True, help="Token mint address to sell.")
@click.option("--pct", required=True, type=float, help="Percent of held balance to sell (0-100].")
@click.option("--slippage-bps", default=150, type=int, help="Max slippage, in basis points.")
@click.option("--dry-run", is_flag=True, help="Preview only — sign and send nothing.")
@click.pass_obj
def memecoin_sell(cfg, token_address, pct, slippage_bps, dry_run):
    """Sell PCT% of the held balance of TOKEN back to SOL."""
    from hf_trading_bot import memecoin

    storage = _load_storage(cfg)
    try:
        settings = storage.get_settings()
        result = memecoin.execute_sell(
            token_address, pct, storage,
            kill_switch=bool(settings["kill_switch_active"]),
            slippage_bps=slippage_bps, dry_run=dry_run)
    except memecoin.MemecoinError as e:
        storage.close()
        raise click.ClickException(str(e))
    finally:
        storage.close()
    if result.get("dry_run"):
        click.echo(f"DRY RUN — would receive ~{result['sol_amount']:.5f} SOL "
                   f"(~${result['usd_amount']:,.2f}). Nothing signed or sent.")
        return
    click.echo(f"SELL submitted: {pct:.0f}% of {token_address} -> "
              f"{result['sol_amount']:.5f} SOL (~${result['usd_amount']:,.2f})")
    click.echo(f"  tx {result['tx_signature']}  status: {result['status']}")
    try:
        from hf_trading_bot import notify
        notify.notify(f"VANTRIX memecoin SELL — {token_address[:8]}…",
                      f"sold {pct:.0f}% -> {result['sol_amount']:.5f} SOL "
                      f"(~${result['usd_amount']:,.2f})\n"
                      f"tx {result['tx_signature']}  status {result['status']}")
    except Exception:  # noqa: BLE001
        pass


@memecoin.command("history")
@click.option("--limit", default=20, type=int)
@click.pass_obj
def memecoin_history(cfg, limit):
    """Every memecoin trade this wallet has made through this bot."""
    storage = _load_storage(cfg)
    try:
        rows = storage.recent_memecoin_trades(limit=limit)
        net = storage.memecoin_net_deployed_usd()
    finally:
        storage.close()
    if not rows:
        click.echo("No memecoin trades recorded yet.")
        return
    for t in rows:
        click.echo(f"#{t['id']:<4} {t['side'].upper():<4} {t['token_address'][:10]}…  "
                   f"${t['usd_amount']:,.2f}  {t['status']:<10} {t['created_at'][:16]}")
    click.echo(f"\nNet deployed: ${net:,.2f}")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address for the local server.")
@click.option("--port", default=8420, type=int, help="Port for the local server.")
@click.option("--refresh", default=15, type=int, help="Seconds between client polls.")
@click.option("--publish", "publish_path", default=None, type=click.Path(),
              help="Write a self-contained static HTML snapshot to this path instead "
                   "of serving live.")
@click.option("--enable-agent-runner", is_flag=True,
              help="Let the dashboard SPAWN a real committee review (via the local "
                   "`claude` CLI) when you issue a command. Off by default: this runs "
                   "Claude with your tools and spends tokens, so it must be opted into.")
@click.option("--runner-permission-mode", default="bypassPermissions",
              help="Permission mode for the spawned `claude -p`. Default bypassPermissions "
                   "so APEX can run committee tools without interactive prompts (there's no "
                   "TTY to answer them). Set to 'default' to keep prompts, or another mode "
                   "your Claude version supports.")
@click.option("--runner-cmd", default=None,
              help="Use a CUSTOM command as the primary agent executor instead of `claude` "
                   "— the prompt is piped to its stdin and stdout is captured as APEX's "
                   "reply. e.g. 'ollama run nemotron'. NOTE: a plain local model can write a "
                   "reply but cannot run committee tools (events, orders) — use `claude` for "
                   "the full tool-driven committee.")
@click.option("--fallback-cmd", default=None,
              help="If the primary executor (claude) fails — including when your Anthropic "
                   "usage/quota is exhausted — automatically retry the command with this one "
                   "(prompt piped to stdin). e.g. 'ollama run nemotron'. Keeps the committee "
                   "answering after tokens run out.")
@click.option("--auto-execute", is_flag=True,
              help="Place agent-proposed orders automatically, with no approval click. "
                   "PAPER ACCOUNTS ONLY — there is no live override for unattended "
                   "placement. Still obeys the kill switch, position caps, buying power "
                   "and the PDT guard, plus a per-order ceiling (--auto-execute-max). "
                   "Off by default: this is the gate that catches a bad agent call.")
@click.option("--auto-execute-max", "auto_execute_max", default=500.0, type=float,
              help="Per-order notional ceiling for --auto-execute (default $500). Anything "
                   "larger stays staged for manual approval.")
@click.option("--committee-model", default=None,
              help="Run the `claude` committee on this model (e.g. 'sonnet' or 'haiku') "
                   "instead of your Claude Code default. Sonnet costs a fraction of Opus "
                   "with near-equal quality on most reviews — the biggest token saver. "
                   "Pair with --tiered so easy asks skip Claude entirely.")
@click.option("--tiered", is_flag=True,
              help="Route easy work to cheap models and keep Claude for the hard calls. "
                   "STUDY buttons then research on a cheap model (NVIDIA/Ollama) and save "
                   "the note directly — no Claude tokens; plainly informational console "
                   "questions answer on a cheap model too. Anything decision-shaped "
                   "(buy/sell/valuation/committee) still routes to Claude. Configure "
                   "providers in .env — see `hf-bot models check`.")
@click.option("--open", "open_browser", is_flag=True,
              help="Open the dashboard in your default browser once the server is up.")
@click.option("--token", default=None,
              help="Require this secret to access the dashboard — essential before "
                   "exposing it via a tunnel. Opened once as ?key=..., then a cookie.")
@click.option("--auth", is_flag=True,
              help="Require a token; auto-generate and print a strong one if --token "
                   "isn't given. Use this for tunnels.")
@click.option("--tunnel", is_flag=True,
              help="Also open a public HTTPS tunnel (cloudflared) so you can reach the "
                   "dashboard from any device. Forces --auth — never exposed without a token.")
@click.pass_obj
def dashboard(cfg: AppConfig, host: str, port: int, refresh: int,
              publish_path: Optional[str], enable_agent_runner: bool,
              runner_permission_mode: str, runner_cmd: Optional[str],
              fallback_cmd: Optional[str], auto_execute: bool,
              auto_execute_max: float, committee_model: Optional[str],
              tiered: bool, open_browser: bool,
              token: Optional[str], auth: bool, tunnel: bool):
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

    import shutil
    import subprocess
    import threading
    import traceback
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from hf_trading_bot.cortex import (
        CommandError,
        apex_prompt,
        extract_symbol,
        parse_console_message,
        parse_tactical_command,
        study_cycle_prompt,
        study_prompt,
        tactical_trade_prompt,
    )
    from hf_trading_bot.curriculum import agent_keys

    # Seeding/migration is done; the startup connection can't be shared across
    # request threads (SQLite forbids it), so close it and give each request
    # its own short-lived connection created in — and used only by — its own
    # thread. build_snapshot is read-only, so concurrent reads are safe.
    from hf_trading_bot.execution import is_paper
    db_path = cfg.db_path
    storage.close()
    refresh_ms = max(2000, refresh * 1000)
    claude_bin = shutil.which("claude") if (enable_agent_runner and not runner_cmd) else None
    # Tiered routing: which cheap/mid model tiers actually have credentials.
    from hf_trading_bot import model_router
    router_avail = model_router.available() if tiered else {}
    tiered_on = tiered and any(router_avail.values())
    # A cheap tier can run study/console work on its own, so it counts as an
    # executor even when `claude` isn't installed.
    have_executor = enable_agent_runner and bool(
        claude_bin or runner_cmd or fallback_cmd or tiered_on)
    # Markers that mean "the primary model is out of budget", so we fail over.
    _QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "credit", "429",
                      "limit reached", "insufficient", "billing", "exceeded")

    # Keep the console lean: after any command finishes, move all but the newest
    # few finished responses to research/committee/ .md files.
    KEEP_CONSOLE = 4

    def _archive_overflow():
        try:
            from hf_trading_bot import committee_archive
            s = Storage(db_path)
            try:
                committee_archive.archive_commands(s, keep=KEEP_CONSOLE)
            finally:
                s.close()
        except Exception:  # archiving is best-effort, never break a run
            pass

    # Live price cache for the watchlist + positions, refreshed on a slow timer
    # in the background (market data, not per-poll) so the dashboard can chart
    # prices without hammering the data source on every request.
    import time as _time
    price_cache: dict = {}

    def _refresh_prices():
        try:
            s = Storage(db_path)
            try:
                syms = {w["symbol"].upper() for w in s.get_watchlist()}
            finally:
                s.close()
            try:
                for p in _build_broker(cfg).get_positions():
                    syms.add(p.symbol.upper())
            except Exception:  # noqa: BLE001
                pass
            syms = sorted(syms)[:24]
            if not syms:
                return
            bars = _build_provider(cfg).daily_bars(syms, lookback_days=60)
            fresh = {}
            for sym, series in (bars or {}).items():
                closes = [b.c for b in series]
                if len(closes) < 2:
                    continue
                fresh[sym] = {"last": closes[-1],
                              "change_pct": (closes[-1] / closes[-2] - 1) * 100,
                              "closes": closes[-48:]}
            price_cache.clear()
            price_cache.update(fresh)
        except Exception:  # noqa: BLE001 — a failed refresh just keeps the last cache
            pass

    def _price_loop():
        while True:
            _refresh_prices()
            _time.sleep(300)   # 5 min — market data doesn't need per-poll pulls

    # Access token: required before exposing the dashboard past this machine.
    # A tunnel ALWAYS forces auth — never expose the committee without a lock.
    if (auth or tunnel) and not token:
        import secrets
        token = secrets.token_urlsafe(18)
    require_auth = bool(token)

    # The runner spawns Claude and spends tokens. Whenever the dashboard is
    # reachable beyond this machine (a tunnel, or a non-localhost bind), the
    # runner MUST be gated by a token — otherwise anyone who can reach it could
    # run up your bill. (--tunnel forces a token on already; this also catches
    # `--host 0.0.0.0 --enable-agent-runner` with no auth.)
    exposed = tunnel or host not in ("127.0.0.1", "localhost")
    if enable_agent_runner and exposed and not require_auth:
        storage.close()
        raise click.ClickException(
            "The agent-runner is exposed without a token, so anyone who can reach "
            "this dashboard could spend your tokens. Add --auth (or --token), and "
            "only you — holding the link and key — can then command a spend."
        )

    def _exec_primary(prompt: str, model: Optional[str] = None):
        """Run the primary executor: a custom --runner-cmd (prompt on stdin) or
        `claude -p` (full tool-driven committee). `model` overrides the launch
        --committee-model for this one run. Returns (returncode, out, err)."""
        import shlex
        if runner_cmd:
            p = subprocess.run(shlex.split(runner_cmd), input=prompt, cwd=".",
                               capture_output=True, text=True, timeout=1800)
        else:
            cmd = [claude_bin, "-p"]
            use_model = model or committee_model
            if use_model:
                cmd += ["--model", use_model]
            if runner_permission_mode and runner_permission_mode != "default":
                cmd += ["--permission-mode", runner_permission_mode]
            cmd.append(prompt)
            p = subprocess.run(cmd, cwd=".", capture_output=True, text=True, timeout=1800)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "")

    def _exec_fallback(prompt: str):
        import shlex
        p = subprocess.run(shlex.split(fallback_cmd), input=prompt, cwd=".",
                           capture_output=True, text=True, timeout=1800)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "")

    def _run_console(command_id: int, prompt: str, label: str,
                     model: Optional[str] = None):
        """Run APEX on a console command in a background thread. Tries the
        primary executor (claude) first; if it fails — including when Anthropic
        usage is exhausted — automatically fails over to --fallback-cmd (e.g. a
        local Ollama model), so the committee keeps answering after tokens run
        out. `model` overrides the committee model for this run. The prompt is
        passed as an argument / on stdin, never via a shell."""
        s = Storage(db_path)
        try:
            s.update_command(command_id, status="running", detail=f"APEX working on: {label}")
        finally:
            s.close()

        reply, ok, detail = "", False, ""
        primary_available = bool(claude_bin or runner_cmd)
        try:
            if primary_available:
                rc, out, err = _exec_primary(prompt, model)
                reply, ok = out, (rc == 0 and bool(out))
                if not ok:
                    blob = (out + " " + err).lower()
                    quota = any(m in blob for m in _QUOTA_MARKERS)
                    if fallback_cmd:
                        frc, fout, ferr = _exec_fallback(prompt)
                        if frc == 0 and fout:
                            reply, ok = fout, True
                            detail = (("Anthropic usage exhausted — " if quota
                                       else "primary executor failed — ")
                                      + f"switched to `{fallback_cmd}`")
                        else:
                            detail = f"primary failed and fallback failed: {(ferr or fout)[:160]}"
                    else:
                        detail = (err or out or "primary executor failed").strip()[:200]
            elif fallback_cmd:
                frc, fout, ferr = _exec_fallback(prompt)
                reply, ok = fout, (frc == 0 and bool(fout))
                detail = "via fallback executor" if ok else (ferr or "fallback failed")[:200]

            if not detail:
                detail = "completed" if ok else "no executor produced a reply"
            s = Storage(db_path)
            try:
                s.update_command(command_id, status="done" if ok else "failed",
                                 detail=detail.replace("\n", " ")[-300:],
                                 reply=reply or (None if ok else "APEX run failed — see detail."))
            finally:
                s.close()
            # An agent may have staged an order; place it if auto-execute is on.
            _auto_execute_pending()
            _archive_overflow()
        except Exception as e:  # noqa: BLE001
            s = Storage(db_path)
            try:
                s.update_command(command_id, status="failed", detail=f"{type(e).__name__}: {e}")
            finally:
                s.close()

    def _handle_order_action(action, proposal_id, auto=False):
        """Approve (place) or reject a proposed order from the dashboard. A
        direct action — no Claude, no tokens — behind the same guards as the
        CLI. Places paper orders only. `auto` marks placements made by the
        auto-execute sweep (no human click) for the notification wording."""
        from hf_trading_bot.execution import ProposedOrder, place, validate

        try:
            proposal_id = int(proposal_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "missing or invalid order id"}
        s = Storage(db_path)
        try:
            r = s.get_order_proposal(proposal_id)
            if not r:
                return {"ok": False, "error": f"no proposal #{proposal_id}"}
            if r["status"] != "proposed":
                return {"ok": False, "error": f"proposal #{proposal_id} is '{r['status']}', not open"}
            if action == "reject":
                s.update_order_proposal(proposal_id, status="rejected",
                                        detail="rejected from dashboard")
                return {"ok": True, "id": proposal_id, "status": "rejected"}
            if action != "approve":
                return {"ok": False, "error": f"unknown action {action!r}"}

            settings = s.get_settings()
            proposal = ProposedOrder(
                symbol=r["symbol"], side=r["side"], qty=r["qty"], est_price=r["est_price"],
                est_notional=r["est_notional"], stop_price=r["stop_price"],
                take_profit=r["take_profit"], decision_id=r["decision_id"],
            )
            try:
                broker = _build_broker(cfg)
                broker.get_daily_bars([proposal.symbol])   # establish a price
                acct = broker.get_account()
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"could not read the broker: {e}"}

            ok, reasons = validate(
                proposal, broker_name=cfg.broker,
                kill_switch=bool(settings["kill_switch_active"]),
                buying_power=acct.buying_power,
            )
            if not ok:
                s.update_order_proposal(proposal_id, status="rejected", detail="; ".join(reasons))
                return {"ok": False, "error": "; ".join(reasons), "id": proposal_id}
            try:
                placed = place(broker, proposal)
            except Exception as e:  # noqa: BLE001
                s.update_order_proposal(proposal_id, status="failed", detail=str(e))
                return {"ok": False, "error": f"placement failed: {e}", "id": proposal_id}
            s.update_order_proposal(proposal_id, status=placed.status or "placed",
                                    detail=f"placed via {cfg.broker}", broker_order_id=placed.id)
            proposal.rationale = r.get("rationale") or ""
            try:
                from hf_trading_bot import notify
                title, body = notify.order_placed_message(
                    proposal, order_id=placed.id or "?", broker=cfg.broker, auto=auto)
                notify.notify(title, body)
            except Exception:  # notification must never break a placed trade
                pass
            return {"ok": True, "id": proposal_id, "status": placed.status or "placed",
                    "order_id": placed.id, "summary": proposal.summary()}
        finally:
            s.close()

    # Auto-execute is only ever armed against a paper broker. A self-firing loop
    # on a real-money account is a different risk category from a human clicking
    # approve, so this is checked once at startup and again per order.
    auto_exec_on = auto_execute and is_paper(cfg.broker)

    def _auto_execute_pending():
        """Place any staged proposals without waiting for an approval click.

        Runs after an agent finishes. Each order still passes `validate` (kill
        switch, buying power, paper-only, min notional) AND the stricter
        `auto_execute_blockers` (paper-only with no override, per-order ceiling).
        Anything that trips a blocker is left staged for manual approval rather
        than rejected — the principal can still approve it deliberately."""
        if not auto_exec_on:
            return
        from hf_trading_bot.execution import ProposedOrder, auto_execute_blockers
        s = Storage(db_path)
        try:
            pending = s.pending_order_proposals()
        finally:
            s.close()
        for r in pending:
            proposal = ProposedOrder(
                symbol=r["symbol"], side=r["side"], qty=r["qty"],
                est_price=r["est_price"], est_notional=r["est_notional"],
                stop_price=r["stop_price"], take_profit=r["take_profit"],
                decision_id=r["decision_id"],
            )
            blockers = auto_execute_blockers(
                proposal, broker_name=cfg.broker, max_notional=auto_execute_max)
            if blockers:
                s = Storage(db_path)
                try:
                    s.update_order_proposal(
                        r["id"], status="proposed",
                        detail="held for manual approval — " + "; ".join(blockers))
                finally:
                    s.close()
                continue
            # Reuse the exact approval path the dashboard button uses, so
            # auto-execution can never take a shortcut around its guards.
            _handle_order_action("approve", r["id"], auto=True)

    _CHEAP_CONSOLE_SYS = (
        "You are APEX, a disciplined investment committee's analyst, answering a "
        "principal's informational question on a fast model. Give a clear, concrete, "
        "professional answer. You have NO live data and NO trading tools, so do not "
        "claim to place orders or quote live prices — reason from general knowledge "
        "and say so. Do not ask questions; answer directly.")

    def _run_study_cheap(command_id: int, agent: str, tier, claude_prompt: str,
                         label: str):
        """Study one agent's next topic on a cheap model and persist it. If the
        cheap tier errors and `claude` is available, fall back to the full
        tool-driven claude study path so a click never silently no-ops."""
        from hf_trading_bot import model_router, study_runner
        s = Storage(db_path)
        try:
            s.update_command(command_id, status="running",
                             detail=f"studying {agent} on {tier} model")
            res = study_runner.study_one(agent, s, tier=tier)
            if not res.get("studied"):
                s.update_command(command_id, status="done",
                                 detail=res.get("reason", "nothing to study"),
                                 reply=f"{agent}: {res.get('reason', 'nothing to study')}.")
            else:
                s.update_command(
                    command_id, status="done",
                    detail=f"studied via {res['provider']} (+1, no Claude tokens)",
                    reply=(f"{res['agent']} studied '{res['topic']}' on "
                           f"{res['provider']} — wrote {res['path']} and recorded "
                           f"it. Library +1."))
            s.close()
            _archive_overflow()
            return
        except model_router.RouterError as e:
            s.close()
            if claude_bin or runner_cmd:
                # cheap tier failed — do it properly on claude instead.
                _run_console(command_id, claude_prompt, label)
            else:
                s2 = Storage(db_path)
                try:
                    s2.update_command(command_id, status="failed",
                                      detail=f"cheap study failed: {str(e)[:180]}")
                finally:
                    s2.close()
        except Exception as e:  # noqa: BLE001
            s3 = Storage(db_path)
            try:
                s3.update_command(command_id, status="failed",
                                  detail=f"{type(e).__name__}: {e}")
            finally:
                s3.close()

    def _run_study_cycle_cheap(command_id: int, rounds: int, tier, claude_prompt: str):
        """Study the next topic for the N least-covered agents on a cheap model,
        persisting each — the STUDY CYCLE button with no Claude tokens. Falls
        back to the claude cycle path if nothing could be studied cheaply."""
        from hf_trading_bot import model_router, study_runner
        from hf_trading_bot.curriculum import agent_keys as _akeys, coverage, next_topic

        s = Storage(db_path)
        try:
            s.update_command(command_id, status="running",
                             detail=f"study cycle on {tier} model ({rounds} round(s))")
            def behind(k):
                a, t = coverage(k, s.studied_topics(k))
                return (a / t if t else 1.0, a)
            ordered = [k for k in sorted(_akeys(), key=behind)
                       if next_topic(k, s.studied_topics(k))]
            done, lines, err = 0, [], None
            for k in ordered:
                if done >= rounds:
                    break
                try:
                    res = study_runner.study_one(k, s, tier=tier)
                except model_router.RouterError as e:
                    err = e
                    break
                if res.get("studied"):
                    lines.append(f"{k}: '{res['topic']}'")
                    done += 1
            if done == 0 and err is not None and (claude_bin or runner_cmd):
                s.close()
                _run_console(command_id, claude_prompt, "study cycle — whole committee")
                return
            report = ("Studied on cheap model — " + "; ".join(lines)) if lines \
                else "Nothing left to study."
            if err is not None:
                report += f" (stopped early: {str(err)[:80]})"
            s.update_command(command_id, status="done",
                             detail=f"cycle +{done} via {tier} (0 Claude tokens)",
                             reply=report)
        finally:
            s.close()
        _archive_overflow()

    def _run_console_cheap(command_id: int, message: str, tier, claude_prompt: str,
                           label: str):
        """Answer an informational console question on a cheap model. On tier
        error, fall back to the claude executor when it's available."""
        from hf_trading_bot import model_router
        s = Storage(db_path)
        try:
            s.update_command(command_id, status="running",
                             detail=f"answering on {tier} model")
        finally:
            s.close()
        try:
            reply = model_router.complete(tier, _CHEAP_CONSOLE_SYS, message)
            ok = bool(reply.strip())
            s = Storage(db_path)
            try:
                s.update_command(
                    command_id, status="done" if ok else "failed",
                    detail=(f"answered on {tier} model (no committee tools)" if ok
                            else "cheap model returned nothing"),
                    reply=reply or None)
            finally:
                s.close()
            _archive_overflow()
        except model_router.RouterError as e:
            if claude_bin or runner_cmd:
                _run_console(command_id, claude_prompt, label)
            else:
                s = Storage(db_path)
                try:
                    s.update_command(command_id, status="failed",
                                     detail=f"cheap tier failed: {str(e)[:180]}")
                finally:
                    s.close()

    def _dispatch_run(kind: str, prompt: str, label: str, symbol=None, message=None,
                      study_agent=None, study_cycle_rounds=None, model=None):
        """Enqueue a run and start it on the executor if one is available;
        otherwise leave it queued for a Claude session. Shared by the APEX
        console and the study buttons so both honor --enable-agent-runner and
        the primary→fallback failover identically.

        With --tiered, easy work is steered to a cheap model first: a per-agent
        STUDY writes its note on the cheap tier (no Claude tokens), and a
        plainly informational console question is answered on the cheap tier.
        Decision-shaped work (buy/sell/valuation/committee) always routes to
        Claude via the normal path."""
        s = Storage(db_path)
        try:
            cid = s.enqueue_command(kind, prompt, symbol=symbol, message=message)
        finally:
            s.close()

        # A tactical trade stages a real order via committee tools, which only
        # the tool-capable executor (claude / --runner-cmd) can run — never a
        # cheap chat model. Say so clearly rather than failing opaquely.
        if kind == "tactical" and not (claude_bin or runner_cmd):
            return {"id": cid, "symbol": symbol, "status": "pending",
                    "message": "A tactical trade needs the full committee (the "
                               "`claude` executor) to stage the order — a cheap "
                               "model can't. Launch with --enable-agent-runner and "
                               "`claude` on PATH."}

        # Tiered steering — only for work that is safe and cheap to offload.
        if have_executor and tiered_on:
            if kind == "study" and study_agent and router_avail.get("cheap"):
                threading.Thread(target=_run_study_cheap,
                                 args=(cid, study_agent, "cheap", prompt, label),
                                 daemon=True).start()
                return {"id": cid, "symbol": symbol, "status": "running",
                        "message": "Studying on a cheap model — no Claude tokens. "
                                   "Watch the Knowledge panel tick up."}
            if kind == "study" and study_cycle_rounds and router_avail.get("cheap"):
                threading.Thread(target=_run_study_cycle_cheap,
                                 args=(cid, int(study_cycle_rounds), "cheap", prompt),
                                 daemon=True).start()
                return {"id": cid, "symbol": symbol, "status": "running",
                        "message": f"Study cycle on a cheap model ({study_cycle_rounds} "
                                   "round(s)) — no Claude tokens. Watch the Knowledge "
                                   "panel."}
            if kind == "console" and model is None:
                tier = model_router.classify("console", message or "")
                if tier in ("cheap", "mid") and router_avail.get(tier):
                    threading.Thread(target=_run_console_cheap,
                                     args=(cid, message or "", tier, prompt, label),
                                     daemon=True).start()
                    return {"id": cid, "symbol": symbol, "status": "running",
                            "message": f"Answering on the {tier} model (saving "
                                       "Claude for the hard calls)."}

        if have_executor:
            threading.Thread(target=_run_console, args=(cid, prompt, label, model),
                             daemon=True).start()
            ack = "Dispatched — watch the cortex; the reply lands in the console."
            status = "running"
        elif enable_agent_runner:
            ack = ("Queued, but no executor was found — install/authenticate the "
                   "`claude` CLI, or pass --runner-cmd, or run it from a Claude "
                   "session (`hf-bot committee queue --run`).")
            status = "pending"
        else:
            ack = ("Queued. Run it from a Claude session "
                   "(`hf-bot committee queue --run`), or start the dashboard with "
                   "--enable-agent-runner so it runs the moment you click.")
            status = "pending"
        return {"id": cid, "symbol": symbol, "status": status, "message": ack}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str, set_cookie: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if set_cookie and require_auth:
                # host-only cookie; carried automatically by same-origin fetches
                self.send_header("Set-Cookie",
                                 f"cortex_key={token}; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)

        def _auth_state(self):
            """(authed, via_query) — via_query means the token arrived as
            ?key=... and we should set the cookie on the response."""
            from urllib.parse import urlparse

            if not require_auth:
                return True, False
            return _token_match(
                token,
                header=self.headers.get("X-Cortex-Key", ""),
                cookie=self.headers.get("Cookie", ""),
                query=urlparse(self.path).query,
            )

        def _deny(self):
            body = (b"<!doctype html><meta charset=utf-8>"
                    b"<body style='font-family:monospace;background:#03060b;color:#dbe9f4;"
                    b"padding:40px'><h3>Live Agent Cortex &mdash; locked</h3>"
                    b"<p>This dashboard requires an access token. Open the link that "
                    b"includes <code>?key=&lt;your token&gt;</code> printed by the server.</p></body>")
            self._send(401, body, "text/html; charset=utf-8")

        def do_GET(self):
            authed, via_query = self._auth_state()
            if not authed:
                self._deny()
                return
            try:
                req_storage = Storage(db_path)
                try:
                    snap = build_snapshot(req_storage)
                finally:
                    req_storage.close()
                if self.path.startswith("/api/snapshot.json"):
                    payload = dataclasses.asdict(snap)
                    payload["runner"] = {
                        "enabled": bool(enable_agent_runner),
                        "claude_available": bool(claude_bin),
                    }
                    payload["prices"] = dict(price_cache)
                    payload.setdefault("system", {})
                    payload["system"]["auto_execute"] = bool(auto_exec_on)
                    payload["system"]["auto_execute_max"] = auto_execute_max
                    self._send(200, json.dumps(payload).encode(), "application/json")
                    return
                html = render_html(snap, mode="live").replace(
                    "window.__CORTEX__ =",
                    f"window.__CORTEX_REFRESH_MS__ = {refresh_ms};\nwindow.__CORTEX__ =",
                    1,
                )
                self._send(200, html.encode(), "text/html; charset=utf-8", set_cookie=via_query)
            except Exception:  # never return an empty response — show the error
                tb = traceback.format_exc()
                body = ("<pre style='color:#f87171;background:#05070a;padding:20px'>"
                        "Cortex dashboard error:\n\n" + tb + "</pre>").encode()
                try:
                    self._send(500, body, "text/html; charset=utf-8")
                except Exception:
                    pass

        def do_POST(self):
            try:
                authed, _ = self._auth_state()
                if not authed:
                    self._send(401, b'{"error":"unauthorized"}', "application/json")
                    return
                # Order approval/rejection — a direct, token-FREE action (no
                # Claude run). Places a paper order behind the same guards.
                if self.path.startswith("/api/order"):
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                    out = _handle_order_action(payload.get("action"), payload.get("id"))
                    code = 200 if out.get("ok") else 400
                    self._send(code, json.dumps(out).encode(), "application/json")
                    return
                # Kill switch — halt/enable order placement. Direct, token-free,
                # writes the same setting as `hf-bot kill-switch`.
                if self.path.startswith("/api/kill-switch"):
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                    active = bool(payload.get("active"))
                    s = Storage(db_path)
                    try:
                        s.set_kill_switch(active)
                    finally:
                        s.close()
                    out = {"ok": True, "kill_switch": active,
                           "message": ("Kill switch ON — trading halted." if active
                                       else "Kill switch OFF — trading enabled.")}
                    self._send(200, json.dumps(out).encode(), "application/json")
                    return
                # Archive the console — move old responses to research/committee/
                # .md files so the dashboard stays clean. A direct, token-free
                # action.
                if self.path.startswith("/api/archive"):
                    from hf_trading_bot import committee_archive
                    s = Storage(db_path)
                    try:
                        written = committee_archive.archive_commands(s, keep=KEEP_CONSOLE)
                    finally:
                        s.close()
                    out = {"ok": True, "archived": len(written),
                           "message": (f"Archived {len(written)} response(s) to "
                                       f"{committee_archive.ARCHIVE_DIR}/."
                                       if written else "Console already clean.")}
                    self._send(200, json.dumps(out).encode(), "application/json")
                    return
                # Study dispatch — send an agent (or the whole committee) to
                # study its next curriculum topic. A real agent run: spends
                # tokens, so it goes through the same executor as the console.
                if self.path.startswith("/api/study"):
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                    study_agent = None
                    cycle_rounds = None
                    if payload.get("cycle"):
                        cycle_rounds = int(payload.get("rounds") or 1)
                        prompt = study_cycle_prompt(cycle_rounds)
                        label = "study cycle — whole committee"
                        symbol = None
                    else:
                        agent = (payload.get("agent") or "").strip()
                        if agent not in agent_keys():
                            self._send(400, json.dumps(
                                {"error": f"unknown agent {agent!r}"}).encode(),
                                "application/json")
                            return
                        prompt = study_prompt(agent)
                        label = f"study next — {agent}"
                        symbol = None
                        study_agent = agent
                    out = _dispatch_run("study", prompt, label, symbol,
                                        study_agent=study_agent,
                                        study_cycle_rounds=cycle_rounds)
                    self._send(200, json.dumps(out).encode(), "application/json")
                    return
                if not self.path.startswith("/api/command"):
                    self._send(404, b'{"error":"not found"}', "application/json")
                    return
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                _payload = json.loads(raw or b"{}")
                text = (_payload.get("text") or "").strip()
                # Model picker: allowlisted so only a known alias reaches the
                # executor; empty means "use the launch --committee-model default".
                pick = (_payload.get("model") or "").strip().lower()
                run_model = pick if pick in ("sonnet", "opus", "haiku") else None
                # Free-form message to APEX. It's passed to the executor as a
                # single subprocess argument (never a shell), so arbitrary text
                # is safe; we only trim/cap it.
                try:
                    message = parse_console_message(text)
                except CommandError as e:
                    self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
                    return
                # Tactical trade — 'trade BTC', 'snipe AAPL'. SNIPER-led, stages
                # a paper order proposal. Always runs the full committee executor
                # (Claude), never a cheap model, because it stages an order.
                tactical = parse_tactical_command(message)
                if tactical is not None:
                    prompt = tactical_trade_prompt(tactical)
                    label = f"tactical trade — {tactical}"
                    out = _dispatch_run("tactical", prompt, label, symbol=tactical,
                                        message=message, model=run_model)
                    if out["status"] == "running":
                        out["message"] = (f"SNIPER is reading {tactical} — a staged "
                                          "order will appear in Orders for approval.")
                    self._send(200, json.dumps(out).encode(), "application/json")
                    return
                symbol = extract_symbol(message)   # best-effort, for display only
                prompt = apex_prompt(message)
                label = message if len(message) <= 60 else message[:57] + "…"
                out = _dispatch_run("console", prompt, label, symbol=symbol,
                                    message=message, model=run_model)
                if out["status"] == "running":
                    out["message"] = ("APEX is on it — watch the cortex; the reply "
                                      "lands in the console.")
                self._send(200, json.dumps(out).encode(), "application/json")
            except Exception:
                tb = traceback.format_exc()
                self._send(500, json.dumps({"error": tb}).encode(), "application/json")

        def log_message(self, *args):
            pass  # silence default stderr access logging

    server = ThreadingHTTPServer((host, port), Handler)
    if not enable_agent_runner:
        runner_note = "  agent-runner off — commands queue for a Claude session to run"
    elif runner_cmd:
        runner_note = (f"  agent-runner ON — executor: `{runner_cmd}` (custom/local model; "
                       "no committee tools)")
    elif claude_bin:
        runner_note = ("  agent-runner ON — commands spawn `claude` "
                       f"(permission-mode: {runner_permission_mode})")
    elif fallback_cmd:
        runner_note = f"  agent-runner ON — no `claude`; using fallback `{fallback_cmd}`"
    else:
        runner_note = ("  agent-runner ON — [!] no `claude` on PATH and no --runner-cmd; "
                       "commands will queue")
    if fallback_cmd and (claude_bin or runner_cmd):
        runner_note += f"  · fallback → `{fallback_cmd}` when the primary fails/runs out"
    if auto_execute and not auto_exec_on:
        runner_note += (f"\n  [!] --auto-execute IGNORED — broker '{cfg.broker}' is not a "
                        "paper account. Unattended placement is paper-only.")
    elif auto_exec_on:
        runner_note += (f"\n  [!] AUTO-EXECUTE ON — agent orders place themselves with no "
                        f"approval click (paper, max ${auto_execute_max:,.0f}/order). "
                        "Kill switch and caps still apply.")
    if tiered:
        if tiered_on:
            live = ", ".join(t for t in ("cheap", "mid") if router_avail.get(t))
            runner_note += (f"  · tiered ON — study & easy asks → {live} model(s), "
                            "hard calls → Claude")
        else:
            runner_note += ("  · [!] --tiered set but no cheap/mid tier configured "
                            "(add keys to .env; see `hf-bot models check`)")
    # The address to actually type in a browser: when bound to all interfaces,
    # localhost still works here, and other devices use this machine's LAN IP.
    local_url = f"http://127.0.0.1:{port}" if host in ("0.0.0.0", "127.0.0.1", "localhost") \
        else f"http://{host}:{port}"
    open_url = f"{local_url}/?key={token}" if require_auth else local_url
    click.echo(f"Live Agent Cortex — {open_url}  (refresh {refresh}s, Ctrl+C to stop)")
    click.echo(runner_note)
    if require_auth:
        click.echo(f"  access token: {token}")
        click.echo("  token required — the ?key=... link above sets a cookie; share "
                   "only with yourself. To expose it, point a tunnel at "
                   f"127.0.0.1:{port} and open <tunnel-url>/?key={token}")
    if host == "0.0.0.0":
        lan_ip = _lan_ip()
        if lan_ip:
            click.echo(f"  on this network (e.g. your phone): http://{lan_ip}:{port}")
        click.echo("  NOTE: bound to all interfaces — anyone on your network can reach "
                   "this. Keep --enable-agent-runner OFF unless you trust the network.")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(local_url)).start()

    threading.Thread(target=_price_loop, daemon=True).start()  # live price cache
    tunnel_proc = _start_tunnel(port, token) if tunnel else None
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        server.server_close()
        if tunnel_proc is not None:
            tunnel_proc.terminate()


def _lan_ip() -> Optional[str]:
    """Best-effort local network IP, so other devices can reach the dashboard.
    Uses a UDP socket that never actually sends anything."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def _start_tunnel(port: int, token: Optional[str]):
    """Spawn a cloudflared quick tunnel to localhost:port and print the public
    URL (with ?key=token) once it appears. Returns the Popen, or None if
    cloudflared isn't installed. Anonymous/ephemeral — no Cloudflare account
    needed. Costs no Claude tokens; it's just a network tunnel."""
    import re
    import shutil
    import subprocess
    import threading

    if shutil.which("cloudflared") is None:
        click.echo(
            "  tunnel: `cloudflared` not found. Install it and retry --tunnel:\n"
            "    Windows: winget install cloudflare.cloudflared\n"
            "    macOS:   brew install cloudflared\n"
            "  (or run `ngrok http {p}` yourself). Serving locally for now.".format(p=port),
            err=True,
        )
        return None

    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    key = f"/?key={token}" if token else ""

    def watch():
        seen = False
        url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        for line in iter(proc.stdout.readline, ""):
            if not seen:
                m = url_re.search(line)
                if m:
                    seen = True
                    click.echo("\n" + "=" * 64)
                    click.echo(f"  PUBLIC LINK (open on any device): {m.group(0)}{key}")
                    click.echo("  Anyone with this link + token can reach your dashboard.")
                    click.echo("=" * 64 + "\n")

    threading.Thread(target=watch, daemon=True).start()
    return proc


def _token_match(expected: str, *, header: str = "", cookie: str = "",
                 query: str = "") -> tuple[bool, bool]:
    """Whether a request is authorized for the dashboard, and whether the token
    arrived via ?key=... (so the caller should set the cookie). Constant-time
    comparison throughout, so a wrong guess leaks no timing signal.

    An empty `expected` means auth is disabled → always authorized.
    """
    import hmac
    from urllib.parse import parse_qs

    if not expected:
        return True, False
    if header and hmac.compare_digest(header, expected):
        return True, False
    for part in cookie.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k == "cortex_key" and hmac.compare_digest(v, expected):
                return True, False
    for v in parse_qs(query).get("key", []):
        if hmac.compare_digest(v, expected):
            return True, True
    return False, False


if __name__ == "__main__":
    cli()
