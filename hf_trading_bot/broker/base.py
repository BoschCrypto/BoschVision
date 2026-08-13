"""Common interface every broker adapter (paper or live) implements. The
strategy engine talks only to this interface, so swapping brokers never
touches strategy or risk logic."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from hf_trading_bot.data.bars import Bar


@dataclass
class Account:
    equity: float
    last_equity: float
    cash: float
    buying_power: float


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    qty: Optional[float]
    type: str
    status: str
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None


class Broker(ABC):
    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_open_orders(self) -> list[Order]: ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> Order: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    def get_daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]: ...
