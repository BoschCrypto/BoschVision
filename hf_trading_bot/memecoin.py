"""Memecoin trading orchestration — the one place a buy or sell actually
happens. Wires together the wallet (solana_wallet), the swap router
(jupiter), and the local ledger (storage) behind explicit, hard guards.

This is deliberately NOT reachable from the committee's automatic pipeline —
no agent, no --auto-execute sweep, no study cycle ever calls into this module.
Every trade here is a direct action the principal invoked by name, with real,
irreversible money. See README's Memecoin trading section before using it.

Guards, in the order they're checked (any one blocks the trade):
  1. HF_BOT_I_UNDERSTAND_MEMECOIN_RISK=true must be set — a deliberate,
     typo-proof opt-in, same pattern as live-equity trading.
  2. The kill switch (the same one Alpaca trading respects) must be off.
  3. The single-trade ceiling (MEMECOIN_MAX_TRADE_USD).
  4. The wallet's cumulative budget (MEMECOIN_WALLET_BUDGET_USD) — net USD
     deployed (buys minus sells) may never exceed this.
None of these limits restrict what the raw private key can do if it is ever
exposed elsewhere — they only bound what THIS CODE will voluntarily spend.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from hf_trading_bot import jupiter, solana_wallet

CONFIRM_ENV = "HF_BOT_I_UNDERSTAND_MEMECOIN_RISK"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEFAULT_MAX_TRADE_USD = 25.0
DEFAULT_BUDGET_USD = 50.0


class MemecoinError(RuntimeError):
    pass


def _env(env: Optional[dict]) -> dict:
    return env if env is not None else os.environ


def is_confirmed(env: Optional[dict] = None) -> bool:
    return _env(env).get(CONFIRM_ENV, "").strip().lower() == "true"


def max_trade_usd(env: Optional[dict] = None) -> float:
    try:
        return float(_env(env).get("MEMECOIN_MAX_TRADE_USD", DEFAULT_MAX_TRADE_USD))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TRADE_USD


def budget_usd(env: Optional[dict] = None) -> float:
    try:
        return float(_env(env).get("MEMECOIN_WALLET_BUDGET_USD", DEFAULT_BUDGET_USD))
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_USD


# ---- pure helpers (no network — the part that's exhaustively unit-tested) --

def cap_reasons(usd_amount: float, *, net_deployed_usd: float,
                budget: float, max_trade: float, kill_switch: bool,
                confirmed: bool) -> list[str]:
    """Every reason a BUY of `usd_amount` must NOT proceed. Empty = safe."""
    reasons: list[str] = []
    if not confirmed:
        reasons.append(
            f"{CONFIRM_ENV} is not set to 'true' — this is real money with no "
            f"paper-trading equivalent; the opt-in must be deliberate")
    if kill_switch:
        reasons.append("kill switch is ON — all trading (including memecoins) is halted")
    if usd_amount <= 0:
        reasons.append("trade amount must be positive")
    if usd_amount > max_trade + 1e-9:
        reasons.append(f"${usd_amount:,.2f} exceeds the per-trade ceiling "
                       f"(${max_trade:,.2f}, MEMECOIN_MAX_TRADE_USD)")
    if net_deployed_usd + usd_amount > budget + 1e-9:
        remaining = max(0.0, budget - net_deployed_usd)
        reasons.append(f"would push net deployed to ${net_deployed_usd + usd_amount:,.2f}, "
                       f"over the ${budget:,.2f} wallet budget "
                       f"(MEMECOIN_WALLET_BUDGET_USD) — ${remaining:,.2f} remaining")
    return reasons


def sell_block_reasons(*, kill_switch: bool, confirmed: bool) -> list[str]:
    """A SELL only needs the universal gates — it reduces exposure, so it is
    never blocked by the budget or per-trade ceiling."""
    reasons: list[str] = []
    if not confirmed:
        reasons.append(f"{CONFIRM_ENV} is not set to 'true'")
    if kill_switch:
        reasons.append("kill switch is ON — all trading is halted")
    return reasons


def lamports_for_usd(usd: float, sol_price_usd: float) -> int:
    if sol_price_usd <= 0:
        raise MemecoinError("invalid SOL price")
    return max(0, int(round(usd / sol_price_usd * 1_000_000_000)))


def usd_for_lamports(lamports: int, sol_price_usd: float) -> float:
    return lamports / 1_000_000_000.0 * sol_price_usd


def token_amount_from_raw(raw: int, decimals: int) -> float:
    return raw / (10 ** decimals) if decimals >= 0 else float(raw)


# ---- network-touching orchestration -----------------------------------

def get_sol_price_usd(*, env: Optional[dict] = None) -> float:
    """SOL/USD via a 1-SOL Jupiter quote into USDC — no separate price feed
    dependency, and it's the same router that will execute the trade."""
    q = jupiter.quote(jupiter.SOL_MINT, USDC_MINT, 1_000_000_000, slippage_bps=50, env=env)
    out = float(q.get("outAmount") or 0)
    if out <= 0:
        raise MemecoinError("could not price SOL (empty Jupiter quote)")
    return out / 1_000_000.0   # USDC has 6 decimals


@dataclass
class TradePreview:
    side: str
    token_address: str
    sol_amount: float
    usd_amount: float
    price_impact_pct: float
    quote: dict


def preview_buy(token_address: str, usd_amount: float, *,
                slippage_bps: int = 100, env: Optional[dict] = None) -> TradePreview:
    """Build the quote for a buy without signing or sending anything. Safe to
    call regardless of guards — this never spends."""
    sol_price = get_sol_price_usd(env=env)
    lamports = lamports_for_usd(usd_amount, sol_price)
    q = jupiter.quote(jupiter.SOL_MINT, token_address, lamports,
                      slippage_bps=slippage_bps, env=env)
    return TradePreview(side="buy", token_address=token_address,
                        sol_amount=lamports / 1_000_000_000.0, usd_amount=usd_amount,
                        price_impact_pct=jupiter.price_impact_pct(q), quote=q)


def execute_buy(token_address: str, usd_amount: float, storage, *,
                kill_switch: bool, slippage_bps: int = 100, dry_run: bool = False,
                env: Optional[dict] = None) -> dict:
    """Buy `token_address` with `usd_amount` of SOL. Raises MemecoinError if
    any guard blocks it — nothing is ever signed or sent in that case."""
    confirmed = is_confirmed(env)
    net = storage.memecoin_net_deployed_usd()
    reasons = cap_reasons(usd_amount, net_deployed_usd=net, budget=budget_usd(env),
                          max_trade=max_trade_usd(env), kill_switch=kill_switch,
                          confirmed=confirmed)
    if reasons:
        raise MemecoinError("; ".join(reasons))

    preview = preview_buy(token_address, usd_amount, slippage_bps=slippage_bps, env=env)
    if dry_run:
        return {"dry_run": True, "side": "buy", "token_address": token_address,
                "usd_amount": usd_amount, "sol_amount": preview.sol_amount,
                "price_impact_pct": preview.price_impact_pct,
                "out_amount_raw": int(preview.quote.get("outAmount") or 0)}

    keypair = solana_wallet.load_keypair(env)
    pub = solana_wallet.pubkey_str(keypair)
    tx_b64 = jupiter.swap_transaction(preview.quote, pub, env=env)
    sig = solana_wallet.sign_and_submit(tx_b64, keypair, env=env)
    status = _await_confirmation(sig, env=env)

    decimals = _safe_decimals(token_address, env=env)
    tokens_out = token_amount_from_raw(int(preview.quote.get("outAmount") or 0), decimals)
    price_usd = (usd_amount / tokens_out) if tokens_out > 0 else None

    trade_id = storage.record_memecoin_trade(
        side="buy", token_address=token_address, token_symbol=None,
        sol_amount=preview.sol_amount, usd_amount=usd_amount, price_usd=price_usd,
        tx_signature=sig, status=status,
        detail=f"price impact {preview.price_impact_pct:.2f}%")
    return {"dry_run": False, "id": trade_id, "side": "buy", "token_address": token_address,
            "usd_amount": usd_amount, "sol_amount": preview.sol_amount,
            "tx_signature": sig, "status": status,
            "price_impact_pct": preview.price_impact_pct}


def execute_sell(token_address: str, pct: float, storage, *,
                 kill_switch: bool, slippage_bps: int = 150, dry_run: bool = False,
                 env: Optional[dict] = None) -> dict:
    """Sell `pct`% (0-100] of the held balance of `token_address` back to SOL."""
    confirmed = is_confirmed(env)
    reasons = sell_block_reasons(kill_switch=kill_switch, confirmed=confirmed)
    if reasons:
        raise MemecoinError("; ".join(reasons))
    if not (0 < pct <= 100):
        raise MemecoinError("--pct must be between 0 (exclusive) and 100")

    keypair = solana_wallet.load_keypair(env)
    pub = solana_wallet.pubkey_str(keypair)
    balance = solana_wallet.get_token_balance(pub, token_address, env=env)
    if not balance or balance["amount_raw"] <= 0:
        raise MemecoinError(f"no balance of {token_address} in this wallet")

    sell_raw = int(balance["amount_raw"] * pct / 100)
    if sell_raw <= 0:
        raise MemecoinError("sell amount rounds to zero")

    q = jupiter.quote(token_address, jupiter.SOL_MINT, sell_raw,
                      slippage_bps=slippage_bps, env=env)
    sol_price = get_sol_price_usd(env=env)
    sol_out = int(q.get("outAmount") or 0) / 1_000_000_000.0
    usd_amount = sol_out * sol_price

    if dry_run:
        return {"dry_run": True, "side": "sell", "token_address": token_address,
                "pct": pct, "sol_amount": sol_out, "usd_amount": usd_amount,
                "price_impact_pct": jupiter.price_impact_pct(q)}

    tx_b64 = jupiter.swap_transaction(q, pub, env=env)
    sig = solana_wallet.sign_and_submit(tx_b64, keypair, env=env)
    status = _await_confirmation(sig, env=env)

    tokens_sold = token_amount_from_raw(sell_raw, balance["decimals"])
    price_usd = (usd_amount / tokens_sold) if tokens_sold > 0 else None

    trade_id = storage.record_memecoin_trade(
        side="sell", token_address=token_address, token_symbol=None,
        sol_amount=sol_out, usd_amount=usd_amount, price_usd=price_usd,
        tx_signature=sig, status=status,
        detail=f"sold {pct:.0f}% of position")
    return {"dry_run": False, "id": trade_id, "side": "sell", "token_address": token_address,
            "pct": pct, "sol_amount": sol_out, "usd_amount": usd_amount,
            "tx_signature": sig, "status": status}


def multi_buy(token_addresses: list[str], usd_each: float, storage, *,
             kill_switch: bool, slippage_bps: int = 100, dry_run: bool = False,
             env: Optional[dict] = None) -> list[dict]:
    """Buy several tokens in sequence, spreading exposure instead of
    concentrating it in one. Each token goes through the exact same
    `execute_buy` — same per-trade ceiling, same cumulative wallet budget — so
    if the budget runs out partway, the remaining tokens are cleanly refused
    and reported, never silently skipped or force-fit. One bad token (no
    route, guard tripped) never stops the rest of the list."""
    results: list[dict] = []
    for addr in token_addresses:
        try:
            r = execute_buy(addr, usd_each, storage, kill_switch=kill_switch,
                            slippage_bps=slippage_bps, dry_run=dry_run, env=env)
            r["token_address"] = addr
            r["ok"] = True
        except MemecoinError as e:
            r = {"token_address": addr, "ok": False, "error": str(e)}
        results.append(r)
    return results


@dataclass
class Position:
    token_address: str
    symbol: Optional[str]
    balance: float
    cost_basis_usd: float
    current_price_usd: Optional[float]
    current_value_usd: Optional[float]
    unrealized_pnl_usd: Optional[float]
    unrealized_pnl_pct: Optional[float]


def list_positions(storage, *, env: Optional[dict] = None) -> list[Position]:
    """Every token this wallet currently holds a nonzero balance of, with a
    live price (when DexScreener has one) and unrealized P/L against the
    local cost-basis ledger. On-chain balance is the source of truth for
    'what's held' — the ledger is only used for cost basis."""
    from hf_trading_bot import memecoin_data

    keypair = solana_wallet.load_keypair(env)
    pub = solana_wallet.pubkey_str(keypair)
    out: list[Position] = []
    for addr in storage.memecoin_distinct_tokens():
        balance = solana_wallet.get_token_balance(pub, addr, env=env)
        if not balance or balance["amount_raw"] <= 0:
            continue
        cost_basis = storage.memecoin_token_net_usd(addr)
        try:
            token = memecoin_data.get_token(addr, env=env)
        except memecoin_data.DexScreenerError:
            token = None
        price = token["price_usd"] if token else None
        value = balance["ui_amount"] * price if price is not None else None
        pnl = (value - cost_basis) if value is not None else None
        pnl_pct = (pnl / cost_basis * 100) if (pnl is not None and cost_basis > 1e-9) else None
        out.append(Position(
            token_address=addr, symbol=(token["symbol"] if token else None),
            balance=balance["ui_amount"], cost_basis_usd=cost_basis,
            current_price_usd=price, current_value_usd=value,
            unrealized_pnl_usd=pnl, unrealized_pnl_pct=pnl_pct))
    return out


def _safe_decimals(token_address: str, *, env: Optional[dict]) -> int:
    try:
        return solana_wallet.get_token_decimals(token_address, env=env)
    except solana_wallet.WalletError:
        return 0   # price_usd becomes None-ish (best-effort only, never blocks)


def _await_confirmation(signature: str, *, env: Optional[dict],
                        attempts: int = 8, delay_s: float = 2.0) -> str:
    """Poll briefly for on-chain confirmation. Returns 'confirmed' or 'failed'
    if resolved within the window, else 'submitted' (still in flight — check
    an explorer). Never raises: a poll failure doesn't mean the trade failed,
    it means we don't know yet, and 'submitted' says exactly that."""
    for _ in range(attempts):
        try:
            result = solana_wallet.get_signature_status(signature, env=env)
        except solana_wallet.WalletError:
            result = None
        if result:
            if result.get("err"):
                return "failed"
            if result.get("confirmed"):
                return "confirmed"
        time.sleep(delay_s)
    return "submitted"
