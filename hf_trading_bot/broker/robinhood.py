"""LIVE broker adapter — places REAL orders with REAL money via the
unofficial `robin_stocks` Robinhood client.

Robinhood has no public sandbox/paper-trading API, so there is no simulated
version of this class. Validate strategies on PaperBroker and the backtester
first; only reach for this once you genuinely intend to risk real funds.
"""
from __future__ import annotations

import os
from typing import Optional

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.data.provider import DataProvider, get_provider
from .base import Account, Broker, Order, Position

_LIVE_TRADING_CONFIRM_ENV = "HF_BOT_I_UNDERSTAND_LIVE_TRADING"


def _is_fractional(qty: float) -> bool:
    return abs(qty - round(qty)) > 1e-9


class RobinhoodBroker(Broker):
    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        mfa_code: Optional[str] = None,
        data_provider: Optional[DataProvider] = None,
    ):
        self._data = data_provider or get_provider()
        if os.environ.get(_LIVE_TRADING_CONFIRM_ENV, "").lower() != "true":
            raise RuntimeError(
                "Refusing to start a live Robinhood broker. Set "
                f"{_LIVE_TRADING_CONFIRM_ENV}=true only once you have validated the "
                "strategy on PaperBroker/backtests and genuinely intend to risk real money."
            )
        try:
            import robin_stocks.robinhood as rh
        except ImportError as e:
            raise RuntimeError(
                "robin_stocks is required for live trading: pip install 'hf-trading-bot[live]'"
            ) from e
        self._rh = rh
        username = username or os.environ.get("ROBINHOOD_USERNAME")
        password = password or os.environ.get("ROBINHOOD_PASSWORD")
        if not username or not password:
            raise RuntimeError("Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD (and mfa_code if enabled).")
        # store_session persists a refresh-token pickle (~/.tokens/robinhood.pickle
        # by default) so unattended runs after the first interactive login don't
        # need to answer an MFA prompt each time.
        rh.login(username=username, password=password, mfa_code=mfa_code, store_session=True)

    def get_account(self) -> Account:
        profile = self._rh.load_portfolio_profile()
        equity = float(profile.get("equity") or 0)
        last_equity = float(profile.get("adjusted_equity_previous_close") or equity)
        cash = float(self._rh.load_account_profile().get("cash") or 0)
        return Account(equity=equity, last_equity=last_equity, cash=cash, buying_power=cash)

    def get_positions(self) -> list[Position]:
        out = []
        for p in self._rh.get_open_stock_positions() or []:
            qty = float(p.get("quantity") or 0)
            if qty <= 0:
                continue
            symbol = self._rh.get_symbol_by_url(p["instrument"])
            avg_price = float(p.get("average_buy_price") or 0)
            quote = self._rh.get_latest_price(symbol)
            current = float(quote[0]) if quote and quote[0] else avg_price
            market_value = qty * current
            out.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=avg_price,
                    current_price=current,
                    market_value=market_value,
                    unrealized_pl=(current - avg_price) * qty,
                    unrealized_plpc=(current / avg_price - 1) if avg_price else 0.0,
                )
            )
        return out

    def get_open_orders(self) -> list[Order]:
        out = []
        for o in self._rh.get_all_open_stock_orders() or []:
            out.append(
                Order(
                    id=o["id"],
                    symbol=self._rh.get_symbol_by_url(o["instrument"]),
                    side=o["side"],
                    qty=float(o["quantity"]) if o.get("quantity") else None,
                    type=o.get("type", "market"),
                    status=o.get("state", "unknown"),
                    stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
                    limit_price=float(o["price"]) if o.get("price") else None,
                )
            )
        return out

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> Order:
        # Fractional quantities are market-only on Robinhood — there's no
        # broker-side stop/limit order type for them. That's fine: the engine
        # already treats `stop_price` on a BUY as metadata to monitor itself
        # (see engine.py's Pass 0 stop-loss check), not an instruction to place
        # a resting stop-entry order, so a plain market buy is always correct
        # here regardless of whether a stop was planned for the position.
        fractional = _is_fractional(qty)
        if side == "buy":
            if fractional:
                res = self._rh.order_buy_fractional_by_quantity(symbol, qty)
            elif order_type == "limit" and limit_price is not None:
                res = self._rh.order_buy_limit(symbol, qty, limit_price)
            else:
                res = self._rh.order_buy_market(symbol, qty)
        elif side == "sell":
            if fractional:
                res = self._rh.order_sell_fractional_by_quantity(symbol, qty)
            elif stop_price is not None:
                res = self._rh.order_sell_stop_loss(symbol, qty, stop_price)
            elif order_type == "limit" and limit_price is not None:
                res = self._rh.order_sell_limit(symbol, qty, limit_price)
            else:
                res = self._rh.order_sell_market(symbol, qty)
        else:
            raise ValueError(f"Unknown side: {side}")
        if not res or "id" not in res:
            raise RuntimeError(f"Robinhood order failed: {res}")
        return Order(
            id=res["id"], symbol=symbol, side=side, qty=qty, type=order_type,
            status=res.get("state", "unconfirmed"), stop_price=stop_price, limit_price=limit_price,
        )

    def cancel_order(self, order_id: str) -> None:
        self._rh.cancel_stock_order(order_id)

    def get_daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        # Daily history comes from the configured data provider, not
        # Robinhood's own historicals endpoint (intraday-oriented and
        # rate-limited). Keeping one data source across all brokers is what
        # makes backtests and live signals directly comparable.
        return self._data.daily_bars(symbols, lookback_days)
