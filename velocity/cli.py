"""Command line for VELOCITY.

    velocity sim          run the agent swarm on a synthetic market
    velocity nulltest     the control: same swarm, driftless random walk
    velocity bench        latency profile of the hot loop
    velocity replay FILE  run against real ticks from CSV
    velocity live         guarded entry point for a real feed

`nulltest` is not an afterthought. Run it whenever you change an agent. If
a change improves `sim` and also improves `nulltest`, the change did not
find edge — it found a way to read noise more confidently, which is worse
than useless because it is persuasive.
"""
from __future__ import annotations

import os
import sys

import click

from .agents.arbiter import Arbiter
from .agents.chartist import Chartist
from .agents.quant import Quant
from .agents.sentinel import Sentinel
from .agents.warden import RiskLimits
from .engine import Engine
from .feeds import ReplayFeed, SyntheticFeed


def _fmt_report(r: dict, show_latency: bool = True) -> str:
    out = []
    out.append(f"  symbol            {r['symbol']}")
    out.append(f"  ticks processed   {r['ticks']:,}")
    if r.get("trades", 0) == 0:
        out.append("  trades            0  (no setup cleared the gates)")
        if r.get("vetoes"):
            out.append(f"  vetoes            {r['vetoes']}")
    else:
        out.append(f"  trades            {r['trades']}  "
                   f"({r['wins']}W / {r['losses']}L, {r['win_rate']:.1%})")
        out.append(f"  avg hold          {r['avg_hold_s']:.1f}s")
        out.append(f"  P&L before costs  ${r['pnl_before_costs']:>12,.2f}")
        out.append(f"  trading costs     ${-r['costs']:>12,.2f}")
        out.append(f"  NET P&L           ${r['pnl']:>12,.2f}   ({r['return_pct']:+.2f}%)")
        out.append(f"  expectancy/trade  ${r['expectancy']:>12,.2f}")
        pf = r["profit_factor"]
        out.append(f"  profit factor     {pf:.2f}" if pf != float("inf") else "  profit factor     inf (no losers)")
        out.append(f"  max drawdown      {r['max_drawdown_pct']:.2f}%")
    if r.get("halted"):
        out.append(f"  HALTED            {r['halt_reason']}")
    if r.get("vetoes") and r.get("trades", 0):
        out.append(f"  risk vetoes       {r['vetoes']}")
    if r.get("slow_lane_errors"):
        out.append(f"  slow-lane errors  {r['slow_lane_errors'][:3]}")
    if show_latency and r.get("latency", {}).get("n"):
        L = r["latency"]
        out.append("  --- hot-loop latency (decision time, 4 agents) ---")
        out.append(f"  p50 {L['p50_ns']/1000:7.1f} us   p99 {L['p99_ns']/1000:7.1f} us   "
                   f"p99.9 {L['p999_ns']/1000:7.1f} us   max {L['max_ns']/1000:7.1f} us")
    return "\n".join(out)


def _build(symbol: str, equity: float, aggressive: bool, horizon: int,
           news_url: str | None, use_model: bool) -> Engine:
    limits = RiskLimits(
        risk_per_trade=0.006 if aggressive else 0.004,
        max_trades_per_min=10 if aggressive else 6,
        horizon_ticks=horizon,
    )
    sentinel = None
    if news_url or use_model:
        sentinel = Sentinel(symbol, feed_url=news_url, use_model=use_model)
    return Engine(
        symbol, equity=equity, limits=limits,
        arbiter=Arbiter(min_agreeing=2 if not aggressive else 1),
        sentinel=sentinel,
    )


@click.group()
@click.version_option("0.1.0", prog_name="velocity")
def cli() -> None:
    """VELOCITY — multi-agent short-term trading. Simulation only by default."""


@cli.command()
@click.option("--ticks", default=200_000, help="How many ticks to generate.")
@click.option("--seed", default=42, help="RNG seed (change it; one seed is one sample).")
@click.option("--equity", default=100_000.0)
@click.option("--symbol", default="SYNTH")
@click.option("--horizon", default=300, help="Expected holding period in ticks; scales stops.")
@click.option("--aggressive", is_flag=True, help="Looser gates. Expect worse risk-adjusted results.")
@click.option("--news-url", default=None, help="JSON headline endpoint for SENTINEL.")
@click.option("--use-model", is_flag=True, help="Let SENTINEL escalate ambiguous headlines to a cheap LLM.")
def sim(ticks, seed, equity, symbol, horizon, aggressive, news_url, use_model):
    """Run the swarm on a synthetic market with trend regimes."""
    eng = _build(symbol, equity, aggressive, horizon, news_url, use_model)
    feed = SyntheticFeed(symbol=symbol, n_ticks=ticks, seed=seed)
    click.echo(f"VELOCITY sim — {ticks:,} ticks, seed {seed}")
    r = eng.run(feed)
    click.echo(_fmt_report(r))
    click.echo(
        "\n  NOTE: this market was generated with built-in trend regimes, so a\n"
        "  trend-following swarm will look good on it by construction. This\n"
        "  number is NOT evidence of edge. Run `velocity nulltest` and then\n"
        "  `velocity replay` on real out-of-sample data."
    )


@cli.command()
@click.option("--ticks", default=200_000)
@click.option("--seeds", default=5, help="How many independent runs.")
@click.option("--equity", default=100_000.0)
@click.option("--horizon", default=300)
def nulltest(ticks, seeds, equity, horizon):
    """Control test: driftless random walk. The swarm SHOULD lose its costs."""
    click.echo(f"VELOCITY null test — {seeds} runs x {ticks:,} driftless ticks")
    click.echo(f"{'seed':>6} {'trades':>7} {'win':>7} {'pre-cost':>12} {'net':>12} {'ret':>8}")
    pre_tot = net_tot = 0.0
    n = 0
    for i in range(seeds):
        seed = 1000 + i * 37
        eng = _build("NULL", equity, False, horizon, None, False)
        r = eng.run(SyntheticFeed(symbol="NULL", n_ticks=ticks, seed=seed, drift_scale=0.0))
        pre_tot += r.get("pnl_before_costs", 0.0)
        net_tot += r.get("pnl", 0.0)
        n += 1
        click.echo(f"{seed:>6} {r.get('trades',0):>7} {r.get('win_rate',0):>6.1%} "
                   f"${r.get('pnl_before_costs',0):>11,.0f} ${r.get('pnl',0):>11,.0f} "
                   f"{r['return_pct']:>+7.2f}%")
    click.echo(f"{'MEAN':>6} {'':>7} {'':>7} ${pre_tot/n:>11,.0f} ${net_tot/n:>11,.0f}")
    click.echo(
        "\n  How to read this: pre-cost P&L near zero means the swarm is NOT\n"
        "  manufacturing signal out of noise, which is the correct behaviour.\n"
        "  A positive null test means a bug or a look-ahead leak, not a\n"
        "  discovery. Investigate it before believing any other number here."
    )


@cli.command()
@click.option("--ticks", default=500_000)
def bench(ticks):
    """Measure hot-loop latency. No trading conclusions, just speed."""
    import time
    eng = _build("BENCH", 100_000.0, False, 300, None, False)
    feed = SyntheticFeed(symbol="BENCH", n_ticks=ticks, seed=5)
    t0 = time.perf_counter()
    r = eng.run(feed)
    wall = time.perf_counter() - t0
    L = r["latency"]
    click.echo(f"VELOCITY bench — {ticks:,} ticks")
    click.echo(f"  wall clock        {wall:.2f}s  ({ticks/wall:,.0f} ticks/sec end-to-end)")
    click.echo(f"  decision latency  p50 {L['p50_ns']/1000:.2f} us")
    click.echo(f"                    p90 {L['p90_ns']/1000:.2f} us")
    click.echo(f"                    p99 {L['p99_ns']/1000:.2f} us")
    click.echo(f"                  p99.9 {L['p999_ns']/1000:.2f} us")
    click.echo(f"                    max {L['max_ns']/1000:.2f} us  (GC pause / OS scheduling)")
    click.echo(
        "\n  Perspective, so this number is not over-read: your decision time is\n"
        "  microseconds, but a retail broker round trip is 20-300 MILLISECONDS\n"
        "  and a colocated HFT firm is sub-microsecond on a direct feed. You\n"
        "  cannot win a latency race. What this buys you is that compute is\n"
        "  never the bottleneck and the swarm can re-evaluate on every tick."
    )


@cli.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--symbol", default=None)
@click.option("--equity", default=None, type=float,
              help="Default 100000, or 5000 with --preset perp.")
@click.option("--horizon", default=None, type=int,
              help="Holding horizon in bars. Default 300, or 144 with --preset perp.")
@click.option("--aggressive", is_flag=True)
@click.option("--preset", type=click.Choice(["perp"]), default=None,
              help="perp: Coinbase perpetual-style futures on 1-minute bars.")
@click.option("--contract-size", default=0.01,
              help="perp preset: underlying per contract (BIP 0.01 BTC, ETP 0.1 ETH).")
@click.option("--fee-bps", default=10.0, help="perp preset: fee per side in bps.")
@click.option("--no-decay-exit", is_flag=True,
              help="perp preset: exit on stop, target or hard flip only.")
@click.option("--record", "record_dir", default=None, type=click.Path(file_okay=False),
              help="Write what every agent said to JSON files in this folder "
                   "(the data behind the agent dashboard).")
def replay(path, symbol, equity, horizon, aggressive, preset, contract_size,
           fee_bps, no_decay_exit, record_dir):
    """Run against real ticks/bars from a CSV. This is the test that counts."""
    feed = ReplayFeed(path, symbol=symbol)
    if preset == "perp":
        from .presets import perp_engine
        eng = perp_engine(feed.symbol, equity=equity or 5_000.0,
                          contract_size=contract_size, horizon_min=horizon or 144,
                          fee_bps=fee_bps, decay_exit=not no_decay_exit)
    else:
        eng = _build(feed.symbol, equity or 100_000.0, aggressive, horizon or 300,
                     None, False)
    click.echo(f"VELOCITY replay — {path}")
    if record_dir:
        from .recorder import Recorder
        rec = Recorder(eng)
        r = rec.run(feed)
        sizes = rec.write(record_dir, command="velocity " + " ".join(sys.argv[1:]),
                          source_note=os.path.basename(path))
        click.echo("  recorded          " + ", ".join(
            f"{k} {n} rows" for k, (n, _b) in sizes.items()) + f"  -> {record_dir}")
    else:
        r = eng.run(feed)
    click.echo(_fmt_report(r))
    b, g = r.get("benchmark") or {}, r.get("significance") or {}
    if b:
        click.echo(f"  buy-and-hold      {b['buy_hold_pct']:+.2f}%   strategy "
                   f"{b['strategy_pct']:+.2f}%   excess {b['excess_pct']:+.2f}%   "
                   f"(in market {b['time_in_market_pct']:.0f}% of bars)")
    if g.get("n", 0) >= 2:
        click.echo(f"  t-statistic       {g['t_stat']:+.2f}  {g['verdict']}")
    click.echo(f"  rows read {feed.rows_read:,}  skipped {feed.rows_skipped:,}")
    if r.get("trades", 0) < 30:
        click.echo(
            f"\n  SAMPLE TOO SMALL: {r.get('trades',0)} trades cannot separate skill\n"
            "  from luck. Under ~30 trades the confidence interval on the win\n"
            "  rate spans most of the plausible range. Get more data before\n"
            "  concluding anything."
        )


@cli.command()
@click.argument("symbol")
@click.option("--days", default=45.0, help="How many days of 1-minute bars.")
@click.option("--out", required=True, type=click.Path(dir_okay=False))
@click.option("--end", "end_ts", default=None, type=int,
              help="Epoch seconds to end at (default now). Use it to pull an "
                   "earlier window as an untouched holdout.")
def fetch(symbol, days, out, end_ts):
    """Download 1-minute Coinbase spot candles (e.g. BTC-USD) to a CSV."""
    from .feeds.coinbase_history import fetch_1m
    info = fetch_1m(symbol, days, out, end_ts=end_ts)
    click.echo(f"{symbol}: {info['bars']:,} bars  {info['first']} -> {info['last']}")
    click.echo(f"  requests {info['requests']}  failed {info['failed']}  gaps {info['gaps']}")
    if info["failed"]:
        click.echo("  some requests failed: the file has holes, fetch again before using it")


@cli.command()
@click.option("--symbol", default="ETH-USD", help="Coinbase spot product to follow.")
@click.option("--contract-size", default=0.1, help="Underlying per contract (0.1 ETH, 0.01 BTC).")
@click.option("--horizon", default=78, help="Holding horizon in minutes (78 ETH, 144 BTC).")
@click.option("--equity", default=5_000.0)
@click.option("--port", default=8765)
@click.option("--host", default="127.0.0.1", help="Use 0.0.0.0 to open it to your network.")
@click.option("--replay", "replay_csv", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Play a CSV instead of following the live market.")
@click.option("--speed", default=10.0, help="Replay speed in bars per second.")
@click.option("--export", "export_path", default=None, type=click.Path(dir_okay=False),
              help="With --replay: write the frames to a JSON file and exit.")
@click.option("--last", "last_bars", default=4320, help="With --export: keep this many final bars.")
@click.option("--log", "log_path", default="velocity-mind-trades.csv", type=click.Path(dir_okay=False),
              help="Live mode: append every closed paper trade to this CSV.")
@click.option("--open", "open_browser", is_flag=True, help="Open the page in your browser.")
@click.option("--news/--no-news", default=True,
              help="Live mode: let SENTINEL read public crypto news feeds.")
def mind(symbol, contract_size, horizon, equity, port, host, replay_csv, speed, export_path,
         last_bars, log_path, open_browser, news):
    """Live view of what every agent is thinking. Paper trading only."""
    import json
    import threading
    import time as _time
    from .mind import MindHub, run_live, run_replay, serve
    from .presets import perp_engine

    eng = perp_engine(symbol, equity=equity, contract_size=contract_size, horizon_min=horizon)
    entry = eng.arbiter.entry_threshold
    if export_path:
        if not replay_csv:
            raise click.UsageError("--export needs --replay FILE")
        hub = MindHub({"symbol": symbol, "mode": "replay", "entry": entry})
        frames = run_replay(hub, eng, ReplayFeed(replay_csv, symbol=symbol), bars_per_s=0)[-last_bars:]
        for f in frames:
            f["ev"] = "|".join(k + "~" + t.replace("|", "/").replace("~", "-") for k, t in f["ev"])
        with open(export_path, "w") as fh:
            json.dump({"rows": frames}, fh, separators=(",", ":"))
        click.echo(f"wrote {len(frames):,} frames to {export_path}")
        return

    mode = "replay" if replay_csv else "live"
    hub = MindHub({"symbol": symbol, "mode": mode, "entry": entry})
    stop = threading.Event()
    srv = serve(hub, host, port)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{srv.server_address[1]}"
    click.echo(f"VELOCITY mind ({mode}, paper trading) -> {url}")
    click.echo(f"To flatten and stop trading, create a file named {eng.warden.limits.kill_switch_path} "
               "in this folder; delete it to resume. Ctrl+C to quit.")
    kwargs = {}
    if replay_csv:
        target, args = run_replay, (hub, eng, ReplayFeed(replay_csv, symbol=symbol), speed, stop)
    else:
        from .mind import TradeLog
        target, args = run_live, (hub, eng, symbol, stop)
        kwargs = {"on_close": TradeLog(log_path, symbol)}
        click.echo(f"Closed paper trades are appended to {log_path}")
        if news:
            from .presets import NEWS_FEEDS
            # RSS arrives minutes late, so a headline's weight halves every
            # 30 minutes rather than every 3.
            kwargs["sentinel"] = Sentinel(symbol, feed_urls=NEWS_FEEDS, interval_s=60.0,
                                          half_life_s=1800.0, ttl_s=3 * 3600.0, timeout_s=10.0)
            click.echo("SENTINEL is reading: " + ", ".join(u.split("/")[2] for u in NEWS_FEEDS))
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True, name="velocity-mind-engine")
    t.start()
    try:
        while t.is_alive():
            _time.sleep(0.5)
        if mode == "replay":
            click.echo("Replay finished. The page stays up; Ctrl+C to stop.")
            while True:
                _time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()


@cli.command()
@click.option("--config", "cfg_path", default="config/shortterm_frozen.json", type=click.Path(dir_okay=False),
              help="The frozen strategy, risk budget and instrument costs.")
@click.option("--state", "state_path", default="velocity-paper-state.json", type=click.Path(dir_okay=False),
              help="Where positions, orders and the balance are kept between restarts.")
@click.option("--log", "log_path", default="velocity-paper-decisions.jsonl", type=click.Path(dir_okay=False),
              help="One line per decision: the rule that made it and what each agent knew.")
@click.option("--once", is_flag=True, help="Warm up, process whatever bars are due, and exit.")
def paper(cfg_path, state_path, log_path, once):
    """Paper-trade the frozen short-term strategy. No real orders are sent."""
    from .shortterm.paper import run_live
    run_live(cfg_path, state_path, log_path, once=once)


@cli.command()
def live():
    """Guarded entry point for a real feed. Refuses by default, on purpose."""
    import os
    if os.environ.get("VELOCITY_I_UNDERSTAND_LIVE_TRADING") != "true":
        click.echo(
            "Refused.\n\n"
            "VELOCITY has no live broker adapter wired in, and that is deliberate.\n"
            "Before this should ever touch money, in this order:\n\n"
            "  1. velocity nulltest        — confirm it finds nothing in noise\n"
            "  2. velocity replay <csv>    — real, out-of-sample ticks, 100+ trades\n"
            "  3. paper-trade it live      — on a real feed, for weeks, journaled\n"
            "  4. only then size it small  — and keep the kill switch reachable\n\n"
            "Steps 1-3 cost nothing but time. Skipping them costs money.\n"
            "A hot path this fast will lose money just as fast as it makes it."
        )
        sys.exit(1)
    click.echo(
        "VELOCITY_I_UNDERSTAND_LIVE_TRADING is set, but no live adapter is\n"
        "implemented in this version. Wire a feed into Engine.on_tick() and an\n"
        "order router into PaperExecutor's interface, then re-run.\n"
        "Kill switch: create the file `.velocity_kill` to flatten everything."
    )
    sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    cli()
