"""The execution bridge: turn an approved committee decision into a real order
on the broker — paper money only, and never without explicit approval.

Design, in one breath: a decision produces a *proposed* order (sized against
your risk limits, nothing sent); you approve it deliberately; only then is it
placed. Every guard the strategy engine already enforces — the kill switch,
per-position caps, buying power, paper-only — is reused here, not re-invented.

Pure logic: it computes and validates proposals and places them through the
Broker interface, so it is fully testable with a fake broker and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .broker.base import Broker, Order

# Brokers that trade simulated money. Anything else is a real-money account and
# is refused unless a caller explicitly opts into live trading (nothing in this
# repo does — live execution is intentionally not wired).
PAPER_BROKERS = frozenset({"paper", "alpaca"})

DEFAULT_POSITION_PCT = 2.0   # if a decision carries no size, a small default
MIN_NOTIONAL = 1.0


class ExecutionError(ValueError):
    """A proposal cannot be built or placed."""


def is_paper(broker_name: str) -> bool:
    return broker_name in PAPER_BROKERS


@dataclass
class ProposedOrder:
    symbol: str
    side: str                       # buy | sell
    qty: float
    est_price: float
    est_notional: float
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    position_pct: Optional[float] = None
    rationale: str = ""
    decision_id: Optional[int] = None

    def summary(self) -> str:
        s = (f"{self.side.upper()} {self.qty:.4f} {self.symbol} "
             f"@ ~${self.est_price:,.2f}  (~${self.est_notional:,.2f})")
        if self.stop_price:
            s += f", stop ${self.stop_price:,.2f}"
        if self.take_profit:
            s += f", target ${self.take_profit:,.2f}"
        return s


def build_proposal(
    *,
    symbol: str,
    side: str,
    price: float,
    equity: float,
    buying_power: float,
    max_position_pct: float,
    position_pct: Optional[float] = None,
    held_qty: float = 0.0,
    held_value: float = 0.0,
    stop_price: Optional[float] = None,
    take_profit: Optional[float] = None,
    decision_id: Optional[int] = None,
    rationale: str = "",
) -> ProposedOrder:
    """Size an order from a decision. A BUY is sized to `position_pct` of equity,
    clamped by the per-symbol cap (`max_position_pct`, counting anything already
    held) and available buying power. A SELL closes the held position.

    Nothing is placed here — this only computes what *would* be sent.
    """
    side = side.lower()
    symbol = symbol.upper()
    if price <= 0:
        raise ExecutionError(f"no valid price for {symbol}")

    if side == "buy":
        pct = position_pct if (position_pct and position_pct > 0) else DEFAULT_POSITION_PCT
        target = equity * pct / 100.0
        symbol_cap = max(0.0, equity * max_position_pct / 100.0 - held_value)
        notional = round(min(target, symbol_cap, max(0.0, buying_power)), 2)
        qty = notional / price if notional > 0 else 0.0
    elif side == "sell":
        if held_qty <= 0:
            raise ExecutionError(f"no {symbol} position to sell")
        qty = held_qty
        notional = round(qty * price, 2)
    else:
        raise ExecutionError(f"unknown side {side!r} (expected buy or sell)")

    return ProposedOrder(
        symbol=symbol, side=side, qty=qty, est_price=price, est_notional=notional,
        stop_price=stop_price, take_profit=take_profit, position_pct=position_pct,
        rationale=rationale, decision_id=decision_id,
    )


def validate(
    proposal: ProposedOrder,
    *,
    broker_name: str,
    kill_switch: bool,
    buying_power: Optional[float] = None,
    allow_live: bool = False,
    min_notional: float = MIN_NOTIONAL,
) -> tuple[bool, list[str]]:
    """Every reason this order must NOT be placed. Empty list ⇒ safe to place.

    These are the same limits the strategy engine honors: paper-only, the kill
    switch, a real notional, and buying power."""
    reasons: list[str] = []
    if not is_paper(broker_name) and not allow_live:
        reasons.append(
            f"broker '{broker_name}' is a real-money account — execution is paper-only "
            f"and live trading is not wired; refused"
        )
    if kill_switch:
        reasons.append(
            "kill switch is ON — trading is halted. Disable it deliberately with "
            "`hf-bot kill-switch --off` to allow orders"
        )
    if proposal.qty <= 0 or proposal.est_notional < min_notional:
        reasons.append(
            f"order notional ${proposal.est_notional:,.2f} is below the ${min_notional:,.0f} minimum"
        )
    if (proposal.side == "buy" and buying_power is not None
            and proposal.est_notional > buying_power + 1e-6):
        reasons.append(
            f"notional ${proposal.est_notional:,.2f} exceeds buying power ${buying_power:,.2f}"
        )
    return (len(reasons) == 0, reasons)


def place(broker: Broker, proposal: ProposedOrder) -> Order:
    """Send an already-validated proposal to the broker. Market order; any
    protective stop rides along where the broker supports it."""
    return broker.place_order(
        proposal.symbol, proposal.qty, proposal.side,
        order_type="market", stop_price=proposal.stop_price,
    )
