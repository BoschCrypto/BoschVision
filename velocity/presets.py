"""Named configurations.

`perp_engine` is the configuration used for the Coinbase CFM perpetual-style
futures tests recorded in VELOCITY.md. Every number in it is one of:

  a fact from the venue     contract size, 10 bps/side taker fee
  derived from volatility   horizon = (3 x round-trip cost / 1m sigma)^2,
                            so cost is at most a third of a 1-sigma move
  a sizing/safety setting   chosen so one contract fits a small account

No signal parameter (thresholds, lookbacks, agent weights) was fitted to
returns. Bars are expected at 1-minute cadence: every lookback below is in
bars, so it means minutes.
"""
from __future__ import annotations

from .agents.arbiter import Arbiter
from .agents.chartist import Chartist
from .agents.quant import Quant
from .agents.warden import RiskLimits
from .engine import Engine
from .execution import ExecConfig


# Public, key-free crypto news feeds SENTINEL reads in live mode.
NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]


def horizon_for(sigma_1m_bps: float, round_trip_bps: float = 20.0,
                cost_share: float = 1.0 / 3.0) -> int:
    """Holding period in minutes at which the round-trip cost is
    `cost_share` of a one-sigma move."""
    if sigma_1m_bps <= 0:
        raise ValueError("sigma must be positive")
    return max(1, round((round_trip_bps / cost_share / sigma_1m_bps) ** 2))


def perp_engine(
    symbol: str = "PERP",
    equity: float = 5_000.0,
    contract_size: float = 0.01,
    horizon_min: int = 144,
    fee_bps: float = 10.0,
    decay_exit: bool = True,
    seed: int | None = 4,
) -> Engine:
    eng = Engine(
        symbol, equity=equity,
        limits=RiskLimits(
            risk_per_trade=0.005, max_notional_pct=0.25,
            daily_loss_limit=0.02, max_drawdown_pct=0.08,
            max_consecutive_losses=3, cooldown_s=1800.0,
            max_trades_per_min=2, max_trades_per_session=8,
            vol_spike_mult=5.0, max_spread_bps=8.0,
            stop_atr_mult=2.0, target_atr_mult=3.0,
            session_max_s=1e9,                 # 24/7 market: no close
            horizon_ticks=horizon_min, min_stop_bps=10.0,
            contract_size=contract_size),
        exec_cfg=ExecConfig(latency_ms=100.0, slippage_bps=0.5, fee_bps=fee_bps,
                            assume_spread_bps=0.05, seed=seed),
        quant=Quant(fast_period=30, slow_period=120, atr_period=60,
                    rsi_period=30, z_window=480, roc_lookback=120),
        chartist=Chartist(fast_window=240, slow_window=1440,
                          atr_period=60, vol_period=120),
        arbiter=Arbiter(entry_threshold=0.28, exit_threshold=0.10,
                        min_agreeing=2, min_hold_s=3600.0, exit_persist=3),
    )
    if not decay_exit:
        # Stops, targets and hard flips only. Tested on untouched data and
        # did not help — see VELOCITY.md. Kept so that result is reproducible.
        eng.arbiter.exit_threshold = -1.0
    return eng


BAR_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def breakout_engine(
    symbol: str = "PERP",
    equity: float = 5_000.0,
    contract_size: float = 0.1,
    channel: int = 20,
    exposure: float = 1.0,
    fee_bps: float = 10.0,
    daily_loss_limit: float = 0.02,
    bar_s: int = 900,
    seed: int | None = 4,
) -> Engine:
    """The channel-breakout bot: long on a new `channel`-bar closing high,
    flat on a new channel/2-bar closing low, at whatever bar length it is fed.

    CHARTIST is the rule and ARBITER follows it. QUANT and SENTINEL still
    read the market and show on the dashboard, but they have no vote: the
    rule that was tested had none, and adding one would be trading something
    untested. WARDEN sizes every position at `exposure` times equity and
    keeps only the safety stops (kill switch, daily loss limit). The
    cooldowns and streak throttles of the old bot are off, because they
    would change the rule.
    """
    from .agents.channel import ChannelChartist
    if bar_s >= 21_600:
        # On bars of six hours or more a daily loss limit can only act after
        # the loss has happened; it would just add an exit and a re-entry.
        daily_loss_limit = 1.0
    return Engine(
        symbol, equity=equity,
        limits=RiskLimits(
            fixed_exposure=exposure, brackets=False, max_notional_pct=exposure,
            daily_loss_limit=daily_loss_limit, max_drawdown_pct=1.0,
            cooldown_s=0.0, max_consecutive_losses=10**9,
            max_trades_per_min=10**9, max_trades_per_session=10**9,
            vol_spike_mult=1e12, max_spread_bps=1e12, session_max_s=1e12,
            contract_size=contract_size),
        exec_cfg=ExecConfig(latency_ms=100.0, slippage_bps=0.5, fee_bps=fee_bps,
                            assume_spread_bps=0.05, seed=seed),
        chartist=ChannelChartist(channel),
        arbiter=Arbiter(weights={"CHARTIST": 1.0}, entry_threshold=0.5, exit_threshold=0.5,
                        min_agreeing=1, news_veto_level=9.0, min_hold_s=0.0, exit_persist=1),
    )
