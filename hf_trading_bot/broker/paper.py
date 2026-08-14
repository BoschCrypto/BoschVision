"""Fully simulated broker. Uses real market data for prices, fake money for
fills — no order ever reaches a real exchange or brokerage account. This is
the default and recommended mode for strategy development."""
from __future__ import annotations

import itertools
from typing import Optional

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.data.provider import DataProvider, get_provider
from .base import Account, Broker, Order, Position

_order_ids = itertools.count(1)


class PaperBroker(Broker):
    def __init__(
        self,
        starting_cash: float = 100_000.0,
        data_provider: Optional[DataProvider] = None,
    ):
        self.cash = starting_cash
        self.last_equity = starting_cash
        self._positions: dict[str, Position] = {}
        self._last_prices: dict[str, float] = {}
        self._data = data_provider or get_provider()

    def _mark_to_market(self) -> None:
        for symbol, pos in self._positions.items():
            price = self._last_prices.get(symbol, pos.avg_entry_price)
            pos.current_price = price
            pos.market_value = pos.qty * price
            pos.unrealized_pl = (price - pos.avg_entry_price) * pos.qty
            pos.unrealized_plpc = (price / pos.avg_entry_price - 1) if pos.avg_entry_price else 0.0

    def get_account(self) -> Account:
        self._mark_to_market()
        equity = self.cash + sum(p.market_value for p in self._positions.values())
        return Account(equity=equity, last_equity=self.last_equity, cash=self.cash, buying_power=self.cash)

    def get_positions(self) -> list[Position]:
        self._mark_to_market()
        return [p for p in self._positions.values() if abs(p.qty) > 1e-9]

    def get_open_orders(self) -> list[Order]:
        # Fills happen synchronously in place_order — nothing is ever left resting.
        return []

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> Order:
        price = self._last_prices.get(symbol)
        if price is None:
            raise ValueError(f"No market price known for {symbol}; fetch bars before placing orders.")
        order_id = f"paper-{next(_order_ids)}"
        notional = price * qty
        if side == "buy":
            if notional > self.cash:
                raise ValueError(f"Insufficient paper cash for {symbol}: need {notional:.2f}, have {self.cash:.2f}")
            self.cash -= notional
            pos = self._positions.get(symbol)
            if pos:
                total_qty = pos.qty + qty
                pos.avg_entry_price = (pos.avg_entry_price * pos.qty + price * qty) / total_qty
                pos.qty = total_qty
            else:
                self._positions[symbol] = Position(symbol, qty, price, price, notional, 0.0, 0.0)
        elif side == "sell":
            pos = self._positions.get(symbol)
            if not pos or pos.qty < qty - 1e-9:
                raise ValueError(f"Cannot sell {qty} {symbol}; not held in paper account.")
            self.cash += notional
            pos.qty -= qty
            if pos.qty <= 1e-9:
                del self._positions[symbol]
        else:
            raise ValueError(f"Unknown side: {side}")
        return Order(
            id=order_id, symbol=symbol, side=side, qty=qty, type=order_type, status="filled",
            stop_price=stop_price, limit_price=limit_price,
        )

    def cancel_order(self, order_id: str) -> None:
        return None

    def get_daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        bars = self._data.daily_bars(symbols, lookback_days)
        for symbol, series in bars.items():
            if series:
                self._last_prices[symbol] = series[-1].c
        return bars
