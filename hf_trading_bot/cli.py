from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

import click
from dotenv import load_dotenv

from hf_trading_bot.backtest import replay, stats
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
@click.pass_obj
def run(cfg: AppConfig, dry_run: bool):
    """Run one strategy evaluation cycle against the configured broker."""
    storage = _load_storage(cfg)
    broker = _build_broker(cfg)
    result = run_strategy_cycle(broker, storage, dry_run=dry_run)
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
@click.pass_obj
def loop(cfg: AppConfig, interval: int, dry_run: bool):
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
            result = run_strategy_cycle(broker, storage, dry_run=dry_run)
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
@click.pass_obj
def backtest(cfg: AppConfig, symbol: str, strategy_key: str, start: str, end: Optional[str]):
    """Backtest one symbol/strategy over a historical date range."""
    provider = _build_provider(cfg)
    bars = provider.daily_bars_range(symbol, start, end)
    if len(bars) < 60:
        raise click.ClickException(f"Not enough historical bars for {symbol} in that range.")
    first, last = bars[0].t[:10], bars[-1].t[:10]
    years = (_dt.date.fromisoformat(last) - _dt.date.fromisoformat(first)).days / 365.25
    trades = replay(bars, strategy_key, {})
    s = stats(trades, years)
    click.echo(f"{symbol} / {strategy_key}  ({first} → {last}, {years:.1f}y, {len(bars)} bars)")
    click.echo(f"  Data source: {source_of(provider)}")
    if s.win_rate is None:
        click.echo("  Trades: 0")
    else:
        click.echo(f"  Trades: {s.total_trades}  Win rate: {s.win_rate:.1f}%")
        click.echo(
            f"  CAGR: {s.cagr:.2f}%  Sharpe: {s.sharpe:.2f}  Max DD: {s.max_drawdown:.2f}%"
            if s.sharpe is not None
            else f"  CAGR: {s.cagr:.2f}%  Max DD: {s.max_drawdown:.2f}%"
        )


if __name__ == "__main__":
    cli()
