"""Market-data provider selection.

The engine, brokers and backtester all fetch bars through a provider chosen
here, so swapping data sources never touches strategy or risk code.

Providers:
  "alpaca"      Alpaca primary, yfinance fallback (default, recommended)
  "alpaca_only" Alpaca with no fallback — fail loudly if it's unavailable
  "yfinance"    yfinance only (the original behaviour)
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

from . import alpaca_data, bars as yfinance_bars
from .bars import Bar

log = logging.getLogger(__name__)


@runtime_checkable
class DataProvider(Protocol):
    name: str

    def daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]: ...

    def daily_bars_range(self, symbol: str, start: str, end: Optional[str] = None) -> list[Bar]: ...


class YFinanceProvider:
    """Scrapes Yahoo Finance. No credentials, but unofficial — Yahoo changes
    these endpoints without notice, which is why it's the fallback and not
    the default."""

    name = "yfinance"

    def daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        return yfinance_bars.fetch_batch_daily_bars(symbols, lookback_days)

    def daily_bars_range(self, symbol: str, start: str, end: Optional[str] = None) -> list[Bar]:
        return yfinance_bars.fetch_daily_bars_range(symbol, start, end)


class AlpacaProvider:
    """Alpaca's official Market Data API, keyed to the user's account."""

    name = "alpaca"

    def daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        return alpaca_data.fetch_batch_daily_bars(symbols, lookback_days)

    def daily_bars_range(self, symbol: str, start: str, end: Optional[str] = None) -> list[Bar]:
        return alpaca_data.fetch_daily_bars_range(symbol, start, end)


def _is_empty(result) -> bool:
    if not result:
        return True
    if isinstance(result, dict):
        return not any(series for series in result.values())
    return False


class FallbackProvider:
    """Tries `primary`, falls back to `secondary` on failure or empty result.

    Two deliberate properties:

    * The fallback is **loud**. A silent switch to scraped data would let a
      broken Alpaca config look perfectly healthy while the bot quietly runs
      on a different data source than you think.
    * The fallback is **all-or-nothing per call**, never per-symbol. Splicing
      Alpaca bars for one symbol together with Yahoo bars for another inside
      a single run would produce a subtly inconsistent view of the market.
    """

    def __init__(self, primary: DataProvider, secondary: DataProvider):
        self.primary = primary
        self.secondary = secondary
        self.name = f"{primary.name}+{secondary.name}"
        self.last_source: Optional[str] = None

    def _call(self, method: str, *args, **kwargs):
        try:
            result = getattr(self.primary, method)(*args, **kwargs)
            if _is_empty(result):
                raise RuntimeError("returned no bars")
            self.last_source = self.primary.name
            return result
        except Exception as exc:  # noqa: BLE001 — any failure should fall back
            log.warning(
                "DATA FALLBACK: %s failed (%s) — falling back to %s for this call. "
                "Bars are now coming from a different source than configured.",
                self.primary.name,
                exc,
                self.secondary.name,
            )
            result = getattr(self.secondary, method)(*args, **kwargs)
            self.last_source = self.secondary.name
            return result

    def daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        return self._call("daily_bars", symbols, lookback_days)

    def daily_bars_range(self, symbol: str, start: str, end: Optional[str] = None) -> list[Bar]:
        return self._call("daily_bars_range", symbol, start, end)


def get_provider(name: str = "alpaca") -> DataProvider:
    if name == "yfinance":
        return YFinanceProvider()
    if name == "alpaca_only":
        return AlpacaProvider()
    if name == "alpaca":
        return FallbackProvider(AlpacaProvider(), YFinanceProvider())
    raise ValueError(
        f"Unknown data_provider: {name!r}. Valid: alpaca, alpaca_only, yfinance."
    )


def source_of(provider: DataProvider) -> str:
    """Which source actually served the last call — for logs and backtest
    headers, so output always names where its bars came from."""
    return getattr(provider, "last_source", None) or provider.name
