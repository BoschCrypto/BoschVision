"""Alpaca broker adapter — PAPER trading by default.

Unlike the Robinhood adapter (which drives an unofficial, ToS-grey client),
this talks to Alpaca's official REST API with your own API keys. Ported
endpoint-for-endpoint from the Apex Trading Hub dashboard's
`src/lib/alpaca.server.ts`.

Safety: the base URL defaults to the paper host and this class REFUSES to
construct against any other host unless HF_BOT_I_UNDERSTAND_LIVE_TRADING=true
is explicitly set. A typo in a config file must never silently route real
orders to a live account.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

from hf_trading_bot.data.bars import Bar
from hf_trading_bot.data.provider import DataProvider, get_provider
from .base import Account, Broker, Order, Position

log = logging.getLogger(__name__)

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
_LIVE_TRADING_CONFIRM_ENV = "HF_BOT_I_UNDERSTAND_LIVE_TRADING"
_TIMEOUT = 30


def _resolve_base_url() -> tuple[str, bool]:
    """Returns (base_url, is_paper).

    Tolerates a trailing "/v2" because that is exactly how Alpaca's own
    dashboard displays the endpoint ("https://paper-api.alpaca.markets/v2") —
    pasting it verbatim would otherwise produce /v2/v2/... on every request.
    """
    raw = os.environ.get("ALPACA_BASE_URL", PAPER_BASE).strip().rstrip("/")
    if raw.endswith("/v2"):
        raw = raw[: -len("/v2")]
    return raw, raw == PAPER_BASE


class AlpacaBroker(Broker):
    def __init__(self, data_provider: Optional[DataProvider] = None):
        base, is_paper = _resolve_base_url()
        if not is_paper and os.environ.get(_LIVE_TRADING_CONFIRM_ENV, "").lower() != "true":
            raise RuntimeError(
                f"Refusing to trade against a non-paper Alpaca endpoint ({base}). "
                f"The paper endpoint is {PAPER_BASE}. If you genuinely intend to "
                f"place REAL orders with REAL money, set "
                f"{_LIVE_TRADING_CONFIRM_ENV}=true — otherwise fix ALPACA_BASE_URL."
            )
        self._base = base
        self.is_paper = is_paper

        key_id = os.environ.get("ALPACA_API_KEY_ID")
        secret = os.environ.get("ALPACA_API_SECRET_KEY")
        if not key_id or not secret:
            raise RuntimeError(
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in your environment "
                "or .env file (see .env.example). Never paste the secret into chat."
            )
        # Paper keys conventionally start with "PK", live keys with "AK". This
        # is a strong convention rather than a guarantee, so it warns loudly
        # instead of hard-failing — the endpoint check above is the real gate.
        if is_paper and not key_id.startswith("PK"):
            log.warning(
                "Alpaca key ID does not start with 'PK' but the PAPER endpoint is "
                "configured. If this is a live key, orders may be rejected — "
                "double-check which key pair you copied."
            )
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "content-type": "application/json",
        }
        self._data = data_provider or get_provider()

    # ---- HTTP helpers -----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = requests.request(
            method, f"{self._base}{path}", headers=self._headers, timeout=_TIMEOUT, **kwargs
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Alpaca {resp.status_code} on {path}: {resp.text[:300]}")
        return resp.json() if resp.text else {}

    # ---- Broker interface -------------------------------------------------

    def get_account(self) -> Account:
        a = self._request("GET", "/v2/account")
        equity = float(a.get("equity") or 0)
        return Account(
            equity=equity,
            last_equity=float(a.get("last_equity") or equity),
            cash=float(a.get("cash") or 0),
            buying_power=float(a.get("buying_power") or 0),
        )

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        for p in self._request("GET", "/v2/positions") or []:
            qty = float(p.get("qty") or 0)
            if qty <= 0:
                continue
            avg = float(p.get("avg_entry_price") or 0)
            out.append(
                Position(
                    symbol=p["symbol"],
                    qty=qty,
                    avg_entry_price=avg,
                    current_price=float(p.get("current_price") or avg),
                    market_value=float(p.get("market_value") or 0),
                    unrealized_pl=float(p.get("unrealized_pl") or 0),
                    unrealized_plpc=float(p.get("unrealized_plpc") or 0),
                )
            )
        return out

    def get_open_orders(self) -> list[Order]:
        raw = self._request("GET", "/v2/orders", params={"status": "open", "nested": "true", "limit": 500})
        out: list[Order] = []
        for o in raw or []:
            out.append(
                Order(
                    id=o["id"],
                    symbol=o["symbol"],
                    side=o["side"],
                    qty=float(o["qty"]) if o.get("qty") else None,
                    type=o.get("type", "market"),
                    status=o.get("status", "unknown"),
                    stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
                    limit_price=float(o["limit_price"]) if o.get("limit_price") else None,
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
        if side not in ("buy", "sell"):
            raise ValueError(f"Unknown side: {side}")

        fractional = abs(qty - round(qty)) > 1e-9
        body: dict[str, Any] = {
            "symbol": symbol,
            "qty": f"{qty:.6f}".rstrip("0").rstrip("."),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        if fractional:
            # Alpaca only accepts fractional quantities as market/day orders.
            # The engine treats a buy's stop_price as metadata it monitors
            # itself (engine.py Pass 0), so dropping it here loses nothing.
            body["type"] = "market"
            body.pop("stop_price", None)
        else:
            if order_type == "stop" and stop_price is not None:
                body["stop_price"] = str(stop_price)
            if order_type == "limit" and limit_price is not None:
                body["limit_price"] = str(limit_price)

        res = self._request("POST", "/v2/orders", json=body)
        if not res or "id" not in res:
            raise RuntimeError(f"Alpaca order failed: {res}")
        return Order(
            id=res["id"],
            symbol=symbol,
            side=side,
            qty=qty,
            type=body["type"],
            status=res.get("status", "accepted"),
            stop_price=stop_price,
            limit_price=limit_price,
        )

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}")

    def get_daily_bars(self, symbols: list[str], lookback_days: int = 220) -> dict[str, list[Bar]]:
        return self._data.daily_bars(symbols, lookback_days)

    # ---- Alpaca extras (not part of the Broker ABC) -----------------------

    def get_clock(self) -> dict:
        """Market open/closed state. Not required by the Broker interface, but
        'the market was closed' is the most common reason a cycle places no
        orders — worth being able to say so explicitly."""
        return self._request("GET", "/v2/clock")

    def get_account_number(self) -> str:
        return str(self._request("GET", "/v2/account").get("account_number") or "")
