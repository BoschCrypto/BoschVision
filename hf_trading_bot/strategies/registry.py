from __future__ import annotations

from typing import Optional

from hf_trading_bot.data.bars import Bar
from .base import StrategyResult
from .momentum import REENTRY_COOLDOWN_DAYS, TREND_ENTRY_THRESHOLD, momentum_signal
from .rsi_mean_reversion import rsi_mean_reversion_signal
from .sma_crossover import sma_crossover_signal
from .macd_crossover import macd_crossover_signal
from .bollinger_breakout import bollinger_breakout_signal

DEFAULT_PARAMS: dict[str, dict] = {
    "momentum_90d": {"lookback": 90},
    "rsi_mean_reversion": {"period": 14, "oversold": 30, "overbought": 60},
    "sma_crossover": {"fast": 20, "slow": 50},
    "macd_crossover": {"fast": 12, "slow": 26, "signal": 9},
    "bollinger_breakout": {"period": 20, "num_std": 2.0},
}

STRATEGY_KEYS = list(DEFAULT_PARAMS.keys())


def evaluate(strategy_key: str, bars: list[Bar], params: Optional[dict] = None) -> StrategyResult:
    p = {**DEFAULT_PARAMS.get(strategy_key, {}), **(params or {})}
    if strategy_key == "momentum_90d":
        return momentum_signal(
            bars,
            lookback=p.get("lookback", 90),
            trend_threshold=p.get("trend_threshold", TREND_ENTRY_THRESHOLD),
            trend_entry_allowed=p.get("trend_entry_allowed", False),
        )
    if strategy_key == "rsi_mean_reversion":
        return rsi_mean_reversion_signal(
            bars, p.get("period", 14), p.get("oversold", 30), p.get("overbought", 60)
        )
    if strategy_key == "sma_crossover":
        return sma_crossover_signal(bars, p.get("fast", 20), p.get("slow", 50))
    if strategy_key == "macd_crossover":
        return macd_crossover_signal(bars, p.get("fast", 12), p.get("slow", 26), p.get("signal", 9))
    if strategy_key == "bollinger_breakout":
        return bollinger_breakout_signal(bars, p.get("period", 20), p.get("num_std", 2.0))
    raise ValueError(f"Unknown strategy key: {strategy_key}")
