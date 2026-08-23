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
import threading
import time
from dataclasses import dataclass
from typing import Optional

from hf_trading_bot import jupiter, rugcheck, solana_wallet

CONFIRM_ENV = "HF_BOT_I_UNDERSTAND_MEMECOIN_RISK"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEFAULT_MAX_TRADE_USD = 25.0
DEFAULT_BUDGET_USD = 50.0

# A single wallet (excluding the LP itself) holding this much of supply is
# the textbook bundled/insider-launch pattern real pump.fun scalpers flag —
# see rugcheck_flags() below.
RUGCHECK_TOP_HOLDER_RED_PCT = 20.0
RUGCHECK_TOP_HOLDER_YELLOW_PCT = 10.0
RUGCHECK_SCORE_RED_THRESHOLD = 80.0   # RugCheck's own 0-100 composite; higher = riskier


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


# ---- mechanical rug-risk screen (pure — the part that's exhaustively tested) --
#
# There are no earnings, filings, or fundamentals for a memecoin — the
# committee's usual valuation/red-team doctrine does not apply. What DOES
# exist is a handful of concrete, checkable facts that catch the most common
# instant-rug patterns. Passing every check here is NOT a recommendation —
# most tokens that pass still go to zero on pure momentum decay. It only
# means the token avoids the specific, well-known traps below.

MIN_LIQUIDITY_RED_USD = 5_000.0
MIN_LIQUIDITY_YELLOW_USD = 20_000.0
VOLUME_LIQUIDITY_RATIO_YELLOW = 20.0
NEW_POOL_HOURS_YELLOW = 24.0


def risk_flags(token: dict, mint_info: dict, *, now_ms: Optional[int] = None) -> list[dict]:
    """Each flag: {"level": "red"|"yellow", "reason": str}. `token` is a
    memecoin_data-normalized dict; `mint_info` is solana_wallet.get_mint_info's
    return. Pure — no network, so this is where the actual judgement logic
    gets exhaustively unit-tested."""
    flags: list[dict] = []

    if mint_info.get("mint_authority"):
        flags.append({"level": "red", "reason":
                     "mint authority NOT revoked — the creator can mint unlimited "
                     "new supply at will and dilute/dump on holders"})
    if mint_info.get("freeze_authority"):
        flags.append({"level": "red", "reason":
                     "freeze authority NOT revoked — the creator can freeze any "
                     "wallet's tokens and block them from ever selling"})

    liq = token.get("liquidity_usd") or 0.0
    if liq < MIN_LIQUIDITY_RED_USD:
        flags.append({"level": "red", "reason":
                     f"very thin liquidity (${liq:,.0f}) — high slippage, may not "
                     f"be sellable in size"})
    elif liq < MIN_LIQUIDITY_YELLOW_USD:
        flags.append({"level": "yellow", "reason":
                     f"low liquidity (${liq:,.0f}) — expect meaningful slippage"})

    vol = token.get("volume_24h_usd") or 0.0
    if liq > 0 and (vol / liq) > VOLUME_LIQUIDITY_RATIO_YELLOW:
        flags.append({"level": "yellow", "reason":
                     f"24h volume is {vol / liq:.0f}x liquidity — often wash "
                     f"trading or extreme volatility, not organic demand"})

    created = token.get("pair_created_at")
    if created and now_ms:
        age_hours = (now_ms - created) / 3_600_000.0
        if 0 <= age_hours < NEW_POOL_HOURS_YELLOW:
            flags.append({"level": "yellow", "reason":
                         f"pool is only {age_hours:.1f}h old — very new and unproven"})

    return flags


def risk_verdict(flags: list[dict]) -> str:
    if any(f["level"] == "red" for f in flags):
        return "HIGH RISK — red flag(s) present"
    if any(f["level"] == "yellow" for f in flags):
        return "ELEVATED RISK — caution flag(s) present"
    return "no obvious red flags detected (still speculative — not investment advice)"


def pumpfun_risk_flags(coin: dict, mint_info: dict) -> list[dict]:
    """The only universally-applicable structural check for a brand-new
    pump.fun coin pre-migration: mint/freeze authority. There is no
    comparable liquidity figure this early — a bonding curve is not a
    liquidity pool — so the liquidity/volume checks in risk_flags() above do
    not apply. This is deliberately a much shorter list; it is not a lesser
    version of the same safety, it is honestly what is checkable this early."""
    flags: list[dict] = []
    if mint_info.get("mint_authority"):
        flags.append({"level": "red", "reason":
                     "mint authority NOT revoked — the creator can mint unlimited "
                     "new supply at will and dilute/dump on holders"})
    if mint_info.get("freeze_authority"):
        flags.append({"level": "red", "reason":
                     "freeze authority NOT revoked — the creator can freeze any "
                     "wallet's tokens and block them from ever selling"})
    return flags


def rugcheck_flags(token_address: str, *, env: Optional[dict] = None) -> list[dict]:
    """A final, best-effort safety check via RugCheck.xyz — called only right
    before a buy actually executes, not for every scanned candidate (keeps
    real-world call volume well under RugCheck's free-tier rate limit, and
    matches what this is for: a last check before money moves).

    Fills the one real gap pumpfun_risk_flags() has no visibility into: a
    single wallet (often funded from the same source as several others in
    the same block) holding an outsized share of supply — the textbook
    bundled/insider-launch pattern. RugCheck being unreachable, or a coin
    not indexed yet, returns no flags — never a reason to block OR to enter;
    it is one more input, not a requirement."""
    try:
        report = rugcheck.get_report(token_address, env=env)
    except rugcheck.RugCheckError:
        return []
    if not report:
        return []
    flags: list[dict] = []
    top = report.get("top_holder_pct")
    if top is not None and top >= RUGCHECK_TOP_HOLDER_RED_PCT:
        flags.append({"level": "red", "reason":
                     f"RugCheck: a single wallet holds {top:.1f}% of supply — "
                     f"a bundled/insider-launch pattern"})
    elif top is not None and top >= RUGCHECK_TOP_HOLDER_YELLOW_PCT:
        flags.append({"level": "yellow", "reason":
                     f"RugCheck: top non-pool holder owns {top:.1f}% of supply"})
    score = report.get("score")
    if score is not None and score >= RUGCHECK_SCORE_RED_THRESHOLD:
        flags.append({"level": "red", "reason":
                     f"RugCheck composite risk score {score:.0f}/100 is very high"})
    return flags


def check_token(token_address: str, *, env: Optional[dict] = None) -> dict:
    """Fetch live data and run the mechanical screen. Read-only — no wallet
    spend, no guards to pass (there's nothing to protect against here)."""
    import time

    from hf_trading_bot import memecoin_data

    token = memecoin_data.get_token(token_address, env=env)
    if not token:
        raise MemecoinError(f"no DexScreener liquidity pool found for {token_address} "
                            f"— either brand new or not tradable yet")
    mint_info = solana_wallet.get_mint_info(token_address, env=env)
    flags = risk_flags(token, mint_info, now_ms=int(time.time() * 1000))
    return {"token": token, "mint_info": mint_info, "flags": flags,
            "verdict": risk_verdict(flags)}


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
            r = _with_jupiter_retry(execute_buy, addr, usd_each, storage, kill_switch=kill_switch,
                                    slippage_bps=slippage_bps, dry_run=dry_run, env=env)
            r["token_address"] = addr
            r["ok"] = True
        except (MemecoinError, jupiter.JupiterError, solana_wallet.WalletError) as e:
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


MAX_NEW_POSITIONS_PER_CYCLE = 2

# Merging PumpPortal's detections in (see run_autotrade_cycle) can put
# dozens of candidates through mint-info checks in one cycle -- each one a
# getAccountInfo call to the same Solana RPC endpoint. Calling that many in
# quick succession with no spacing tripped Helius's rate limit in live
# testing ("Solana RPC HTTP 429: Too Many Requests"), the same class of
# problem pumpfun_live.py's getTransaction throttle already exists to
# avoid -- same fix, applied here. A simple shared last-call timestamp
# under a lock is enough since this loop is sequential within one cycle;
# the lock only matters if a manual "run cycle now" trigger and the
# background loop ever overlap.
DEFAULT_MINT_CHECK_MIN_INTERVAL_S = 0.35   # ~3/sec ceiling, matching pumpfun_live.py's default
_mint_check_lock = threading.Lock()
_last_mint_check_at = 0.0


def mint_check_min_interval_s(env: Optional[dict] = None) -> float:
    e = env if env is not None else os.environ
    try:
        return max(0.0, float(e.get("MEMECOIN_MINT_CHECK_MIN_INTERVAL_S",
                                    DEFAULT_MINT_CHECK_MIN_INTERVAL_S)))
    except (TypeError, ValueError):
        return DEFAULT_MINT_CHECK_MIN_INTERVAL_S


def _rate_limited_get_mint_info(address: str, *, env: Optional[dict] = None) -> dict:
    global _last_mint_check_at
    min_interval = mint_check_min_interval_s(env)
    with _mint_check_lock:
        wait = min_interval - (time.monotonic() - _last_mint_check_at)
        if wait > 0:
            time.sleep(wait)
        _last_mint_check_at = time.monotonic()
    return solana_wallet.get_mint_info(address, env=env)


# A brief, bounded retry for ONE specific failure mode: PumpPortal's
# subscribeNewToken can notify of a new mint before that mint's account is
# necessarily visible yet via our own RPC node's getAccountInfo -- a real
# race condition introduced by detecting coins faster than a single RPC
# call can confirm them, not a sign the mint doesn't exist. Total added
# delay is small and bounded; a genuinely nonexistent/malformed mint still
# fails after this, just not on the very first millisecond.
_MINT_INFO_RETRY_DELAYS_S = (0.5, 1.0)


def _get_mint_info_for_fresh_candidate(address: str, *, env: Optional[dict]) -> dict:
    last_error: solana_wallet.WalletError = None
    for delay in (0.0,) + _MINT_INFO_RETRY_DELAYS_S:
        if delay:
            time.sleep(delay)
        try:
            return _rate_limited_get_mint_info(address, env=env)
        except solana_wallet.WalletError as e:
            last_error = e
            if "no on-chain account" not in str(e):
                raise   # a different failure mode -- retrying won't help
    raise last_error


_JUPITER_RETRY_DELAYS_S = (1.0, 3.0, 5.0)


def _with_jupiter_retry(fn, *args, **kwargs):
    """Retry ONLY on jupiter.JupiterError -- a network/DNS failure reaching
    Jupiter's quote or swap-transaction endpoint. Both of those calls
    happen strictly BEFORE anything is signed or broadcast (see
    jupiter.py's quote()/swap_transaction()), so a JupiterError means no
    money has moved yet and a retry can never cause a double-spend. Live
    testing showed "Jupiter unreachable: [Errno 11001] getaddrinfo failed"
    hitting real, already-passing candidates at the exact moment of
    execution -- a genuinely missed trade each time, not just noise.
    MemecoinError and solana_wallet.WalletError are deliberately NOT
    retried here: a WalletError can occur AFTER a transaction is already
    submitted (e.g. a timeout waiting for confirmation), where blindly
    retrying risks buying/selling twice."""
    last_error = None
    for delay in (0.0,) + _JUPITER_RETRY_DELAYS_S:
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except jupiter.JupiterError as e:
            last_error = e
    raise last_error


def run_exit_check(storage, *, env: Optional[dict] = None, scalp: bool = False) -> dict:
    """Check every held position against its exit rule and sell if
    triggered. This is the risk-critical half of run_autotrade_cycle,
    factored out so it can run on its own, much faster cadence than entry
    scanning does — checking a handful of held positions costs nothing on
    the free-tier rate limits (RugCheck, pump.fun) that force entry
    scanning to stay slow, so there's no reason a stop-loss should have to
    wait a full 60-120s entry cycle to fire. The dashboard runs this on a
    short independent loop; run_autotrade_cycle calls it too, for its own
    exits section, so there is exactly one implementation of this logic.

    Same never-raises-for-one-token contract as run_autotrade_cycle.
    `held_addresses` in the report is the set of currently-held token
    addresses on success, or None if the positions fetch itself failed
    (already recorded in `errors`) — callers building an entry list from
    this need to know the difference between "no positions" and "couldn't
    find out."""
    from hf_trading_bot import memecoin_data, memecoin_strategy

    report: dict = {"exits": [], "errors": [], "skipped": None, "held_addresses": None}

    kill_switch = bool(storage.get_settings()["kill_switch_active"])
    if kill_switch:
        report["skipped"] = "kill switch is ON"
        return report
    if not is_confirmed(env):
        report["skipped"] = f"{CONFIRM_ENV} is not set"
        return report

    try:
        positions = list_positions(storage, env=env)
    except (MemecoinError, solana_wallet.WalletError) as e:
        report["errors"].append({"stage": "positions", "error": str(e)})
        return report

    report["held_addresses"] = {p.token_address for p in positions}
    for p in positions:
        try:
            basis = storage.memecoin_position_basis(p.token_address)
            if not basis["avg_entry_price"] or not p.current_price_usd:
                continue
            peak = storage.memecoin_update_peak(p.token_address, p.current_price_usd)
            state = storage.memecoin_peak_state(p.token_address) or {}
            hours_held = 0.0
            if basis["first_buy_at"]:
                from datetime import datetime, timezone
                first = datetime.fromisoformat(basis["first_buy_at"].replace("Z", "+00:00"))
                hours_held = (datetime.now(timezone.utc) - first).total_seconds() / 3600.0
            try:
                token = memecoin_data.get_token(p.token_address, env=env)
            except memecoin_data.DexScreenerError:
                token = None

            exit_fn = memecoin_strategy.scalp_exit_signal if scalp else memecoin_strategy.exit_signal
            sig = exit_fn(
                entry_price_usd=basis["avg_entry_price"], current_price_usd=p.current_price_usd,
                peak_price_usd=peak, hours_held=hours_held, token=token,
                already_trimmed_1=bool(state.get("trimmed_1")),
                already_trimmed_2=bool(state.get("trimmed_2")))
            if not sig.exit:
                continue
            result = _with_jupiter_retry(execute_sell, p.token_address, sig.sell_pct, storage,
                                         kill_switch=kill_switch, env=env)
            trim1_pct = (memecoin_strategy.SCALP_TRIM_1_SELL_PCT if scalp
                        else memecoin_strategy.TRIM_1_SELL_PCT)
            trim2_pct = (memecoin_strategy.SCALP_TRIM_2_SELL_PCT if scalp
                        else memecoin_strategy.TRIM_2_SELL_PCT)
            if sig.sell_pct >= 99.9:
                storage.memecoin_clear_position_state(p.token_address)
            elif abs(sig.sell_pct - trim1_pct) < 1e-6 and not state.get("trimmed_1"):
                storage.memecoin_mark_trimmed(p.token_address, 1)
            elif abs(sig.sell_pct - trim2_pct) < 1e-6 and not state.get("trimmed_2"):
                storage.memecoin_mark_trimmed(p.token_address, 2)
            report["exits"].append({"token_address": p.token_address, "symbol": p.symbol,
                                    "sell_pct": sig.sell_pct, "reason": sig.reason,
                                    "result": result})
        except (MemecoinError, jupiter.JupiterError, solana_wallet.WalletError) as e:
            # A transient Jupiter/RPC failure on ONE position must never
            # abort checking the rest — this is the fast, risk-critical
            # loop; letting an exception propagate here would skip every
            # other held position's exit check for this pass, the opposite
            # of what a stop-loss check is for.
            report["errors"].append({"stage": "exit", "token_address": p.token_address,
                                     "error": str(e)})

    return report


_MARKET_CAP_FETCH_MAX_WORKERS = 5   # bounded -- not unlimited, to avoid bursting past pump.fun's rate limit


def enrich_candidates_with_market_cap(candidates: list[dict], *, env: Optional[dict] = None,
                                      max_workers: int = _MARKET_CAP_FETCH_MAX_WORKERS
                                      ) -> tuple[list[dict], dict]:
    """Fetch live market cap (pumpfun_data.get_coin()) for every candidate
    missing one, IN PARALLEL with bounded concurrency — not one network call
    at a time inside the scoring loop (which is what this replaces), and
    not unlimited either, since pump.fun's free-tier API would likely
    reject a full-blast burst. With a merged pool of dozens of candidates
    per cycle (pumpfun_live.py + PumpPortal detections), sequential fetches
    were spending a meaningful chunk of each entry cycle just waiting on
    one REST call at a time before the next could even start.

    Returns (enriched_candidates, outcomes) — outcomes maps address to
    {"status": "already_present"|"fetched"|"no_data"|"error", ...}, for
    callers that want to show what happened (the dashboard's screen
    diagnostic); run_autotrade_cycle itself only needs the enriched list.
    A fetch failure for one candidate never affects any other, same
    "missing data is not a red flag" philosophy as the sequential version."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from hf_trading_bot import pumpfun_data

    outcomes: dict[str, dict] = {}
    need_fetch = []
    for c in candidates:
        if c.get("market_cap_usd") is not None:
            outcomes[c["address"]] = {"status": "already_present"}
        else:
            need_fetch.append(c)

    def _fetch(coin):
        try:
            return coin["address"], pumpfun_data.get_coin(coin["address"], env=env), None
        except pumpfun_data.PumpFunError as e:
            return coin["address"], None, e

    if need_fetch:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch, c) for c in need_fetch]
            for future in as_completed(futures):
                address, fresh, error = future.result()
                if error is not None:
                    outcomes[address] = {"status": "error", "error": str(error)}
                elif fresh and fresh.get("market_cap_usd") is not None:
                    outcomes[address] = {"status": "fetched", "fresh": fresh}
                else:
                    outcomes[address] = {"status": "no_data"}

    enriched = []
    for c in candidates:
        outcome = outcomes.get(c["address"], {})
        if outcome.get("status") == "fetched":
            fresh = outcome["fresh"]
            c = dict(c, market_cap_usd=fresh["market_cap_usd"],
                    price_usd=fresh.get("price_usd"),
                    has_social_links=fresh.get("has_social_links"))
        enriched.append(c)
    return enriched, outcomes


def run_autotrade_cycle(storage, *, env: Optional[dict] = None,
                        max_new_positions: int = MAX_NEW_POSITIONS_PER_CYCLE,
                        scalp: bool = False,
                        live_candidates: Optional[list] = None,
                        pumpportal_feed=None) -> dict:
    """One full autonomous pass: check exits on every held position FIRST (a
    stop-loss always gets first claim on attention and budget), then look for
    new entries with whatever budget remains. Returns a report of every
    action taken, skipped, or errored — never raises for a single token's
    failure, so one bad RPC call or a token that goes illiquid never stops
    the rest of the pass or a later cycle.

    `scalp=True` switches BOTH halves together — exits use the tight
    SCALP_* thresholds (memecoin_strategy.scalp_exit_signal), and new-entry
    discovery reads pump.fun's own new-coin feed instead of DexScreener's
    trending list, scored by pumpfun_entry_signal — the only universal
    safety check on a coin this fresh is mint/freeze authority,

    `live_candidates`, when scalp=True, is used as the entry-discovery list
    INSTEAD of calling pumpfun_data.list_new_coins() — this is how the
    dashboard passes real-time detections from pumpfun_live.LiveFeed
    (a persistent WebSocket subscription) rather than REST-polling once per
    cycle. Ignored when scalp=False. A caller not wired to a live feed
    (the CLI, tests) simply omits it and gets the REST-polling behavior.

    `pumpportal_feed`, when scalp=True, does two things: supplies
    buyer_stats (distinct buyer count) per candidate to
    pumpfun_entry_signal, and its recent_new_coins() are MERGED into the
    candidate list (deduplicated by address, re-sorted newest-first) —
    pumpfun_live.py's RPC-based feed can only afford a handful of
    getTransaction calls/sec across ALL pump.fun activity, so it likely
    misses most real new coins; PumpPortal's subscribeNewToken sees every
    creation with no rate limit at all. Without pumpportal_feed, scoring
    falls back to freshness + SOL-raised alone and candidates come only
    from live_candidates/REST polling. Also ignored when scalp=False;
    None is the correct default when no feed is running.
    there is no liquidity figure yet the way DexScreener has one. That is a
    real, deliberate increase in risk, not a smaller version of the normal
    screen; it is what trading a coin this early means.

    This is the function both the dashboard's background loop and a manual
    'run once' trigger call — the loop is just this on a timer."""
    import time as _time

    from hf_trading_bot import memecoin_data, memecoin_strategy

    report: dict = {"exits": [], "entries": [], "errors": [], "skipped": None}

    exit_report = run_exit_check(storage, env=env, scalp=scalp)
    report["exits"] = exit_report["exits"]
    report["errors"].extend(exit_report["errors"])
    if exit_report["skipped"] is not None:
        report["skipped"] = exit_report["skipped"]
        return report
    if exit_report["held_addresses"] is None:
        # The positions fetch itself failed (already recorded in errors
        # above) -- nothing more can be evaluated this pass.
        return report
    held_addresses = exit_report["held_addresses"]

    kill_switch = bool(storage.get_settings()["kill_switch_active"])

    # 2. New entries — only with whatever budget remains after exits above.
    net = storage.memecoin_net_deployed_usd()
    budget = budget_usd(env)
    remaining = budget - net
    trade_size = max_trade_usd(env)
    if remaining < trade_size * 0.5:
        return report

    if scalp:
        from hf_trading_bot import pumpfun_data
        if live_candidates is not None:
            candidates = list(live_candidates)
        else:
            try:
                candidates = pumpfun_data.list_new_coins(limit=30, env=env)
            except pumpfun_data.PumpFunError as e:
                report["errors"].append({"stage": "scan", "error": str(e)})
                return report
        if pumpportal_feed is not None:
            # pumpfun_live.py's RPC feed can only afford ~3 getTransaction
            # calls/sec across ALL pump.fun activity (creates, buys, AND
            # sells) to avoid tripping Helius's rate limit -- given real
            # pump.fun volume, that budget is mostly consumed by unrelated
            # buy/sell traffic, so it likely misses most actual new coins.
            # PumpPortal's subscribeNewToken sees every creation event with
            # no rate limit at all. Merging it in closes that detection gap
            # rather than replacing either existing source (see
            # pumpportal_live.py's module docstring for the full reasoning).
            seen = {c["address"] for c in candidates}
            for c in pumpportal_feed.recent_new_coins(30):
                if c["address"] not in seen:
                    candidates.append(c)
                    seen.add(c["address"])
            candidates.sort(key=lambda c: c.get("created_at_ms") or 0, reverse=True)
        picked = [c for c in candidates if c["address"] not in held_addresses][:30]
        picked, _mc_outcomes = enrich_candidates_with_market_cap(picked, env=env)
    else:
        try:
            candidates = memecoin_data.trending(limit=30, env=env)
        except memecoin_data.DexScreenerError as e:
            report["errors"].append({"stage": "scan", "error": str(e)})
            return report
        picked = memecoin_data.filter_candidates(
            candidates, min_liquidity_usd=memecoin_strategy.MIN_LIQUIDITY_FOR_ENTRY_USD,
            limit=30, exclude=held_addresses)

    now_ms = int(_time.time() * 1000)
    bought = 0
    for t in picked:
        if bought >= max_new_positions or remaining < trade_size * 0.5:
            break
        try:
            mint_info = (_get_mint_info_for_fresh_candidate(t["address"], env=env) if scalp
                        else _rate_limited_get_mint_info(t["address"], env=env))
        except solana_wallet.WalletError as e:
            report["errors"].append({"stage": "entry-mint-check", "token_address": t["address"],
                                     "error": str(e)})
            continue
        if scalp:
            # Market cap (and price/social-links) are already enriched, in
            # parallel, by enrich_candidates_with_market_cap() above — real
            # testing showed Photon's own >=$10k Memescope filter catching
            # coins with dozens to hundreds of holders in their first
            # minute, a far more direct signal than buyer-diversity
            # counting has proven to be so far.
            buyer_stats = pumpportal_feed.buyer_stats(t["address"]) if pumpportal_feed else None
            sig = memecoin_strategy.pumpfun_entry_signal(t, mint_info, buyer_stats=buyer_stats)
        else:
            sig = memecoin_strategy.entry_signal(t, mint_info, now_ms=now_ms)
        if not sig.enter:
            continue
        rc_flags = rugcheck_flags(t["address"], env=env)
        rc_red = [f["reason"] for f in rc_flags if f["level"] == "red"]
        if rc_red:
            report["errors"].append({"stage": "entry-rugcheck-veto", "token_address": t["address"],
                                     "error": "; ".join(rc_red)})
            continue
        size = min(trade_size, remaining)
        try:
            result = _with_jupiter_retry(execute_buy, t["address"], size, storage,
                                         kill_switch=kill_switch, env=env)
            report["entries"].append({"token_address": t["address"], "symbol": t["symbol"],
                                      "score": sig.score, "usd_amount": size, "result": result})
            remaining -= size
            bought += 1
        except (MemecoinError, jupiter.JupiterError, solana_wallet.WalletError) as e:
            # Same reasoning as run_exit_check: a transient network failure
            # on ONE candidate must not abort scoring/buying the rest of
            # this cycle's picked list.
            report["errors"].append({"stage": "entry-buy", "token_address": t["address"],
                                     "error": str(e)})

    return report


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
