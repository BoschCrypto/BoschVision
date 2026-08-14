from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Risk settings that may be set from settings.yaml. `kill_switch_active` is
# deliberately NOT here: it is runtime state, not config. If YAML could set
# it, every cycle would re-apply the configured value and silently undo a
# kill switch that the daily-loss guard or drawdown circuit breaker had just
# tripped. Flip it with `hf-bot kill-switch` instead.
RISK_KEYS = (
    "exits_allowed_when_paused",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "max_weekly_loss_pct",
    "max_consecutive_losses",
    "max_position_pct",
    "max_portfolio_exposure_pct",
    "max_open_positions",
    "risk_per_trade_pct",
    "atr_stop_multiple",
    "stop_loss_pct",
    "take_profit_r",
    "trailing_stop_pct",
    "trail_activate_r",
    "max_day_trades",
)


@dataclass
class AppConfig:
    db_path: str = "trading_bot.db"
    # "paper"     — fully simulated fills, no credentials, no account touched
    # "alpaca"    — Alpaca PAPER account by default (real API, simulated money)
    # "robinhood" — LIVE real money, opt-in; dormant, see README
    broker: str = "paper"
    # "alpaca" (Alpaca primary + yfinance fallback), "alpaca_only", "yfinance"
    data_provider: str = "alpaca"
    starting_cash: float = 100_000.0
    watchlist: list[dict] = field(default_factory=list)
    risk: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        path = path or os.environ.get("HF_BOT_CONFIG", "config/settings.yaml")
        p = Path(path)
        if not p.exists():
            return cls()
        import yaml

        data = yaml.safe_load(p.read_text()) or {}
        defaults = cls()
        risk = {k: v for k, v in (data.get("risk") or {}).items() if k in RISK_KEYS}
        unknown = set((data.get("risk") or {})) - set(RISK_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown risk setting(s) in {path}: {', '.join(sorted(unknown))}. "
                f"Valid keys: {', '.join(RISK_KEYS)}"
            )
        return cls(
            db_path=data.get("db_path", defaults.db_path),
            broker=data.get("broker", defaults.broker),
            data_provider=data.get("data_provider", defaults.data_provider),
            starting_cash=data.get("starting_cash", defaults.starting_cash),
            watchlist=data.get("watchlist", []),
            risk=risk,
        )
