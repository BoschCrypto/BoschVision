"""Memecoin momentum: what to look for, when to enter, when to exit.

A memecoin has no earnings, no filings, no moat — the committee's equity
doctrine (valuation, red-team, index-hurdle) doesn't apply. What's left is
price/volume/liquidity behavior, and the specific structural traps in
memecoin.risk_flags(). This module is the judgment layer built from exactly
that: DexScreener's momentum fields (already free, already fetched) and the
mechanical risk screen — nothing paid, nothing new to configure.

Be honest about what this is: momentum signals describe what a token has
JUST done, never what it will do next. A high score means the tape looks
active and buy-pressured right now — it is not a prediction, and most
momentum eventually reverses without warning. Treat every number here as
"this is what's true this second," not "this is safe."

Three layers, each independently testable:
  momentum_score()  -- 0-100 composite from price/volume/pressure signals
  entry_signal()     -- momentum_score + risk_flags -> enter or not, and why
  exit_signal()       -- given a live position, what to do about it and why
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hf_trading_bot import memecoin

# --- entry thresholds --------------------------------------------------

MIN_ENTRY_SCORE = 60.0
MIN_LIQUIDITY_FOR_ENTRY_USD = 20_000.0

# --- exit thresholds -----------------------------------------------------
# Wide by memecoin standards on purpose: normal equity stop-loss discipline
# (5-10%) would exit on ordinary noise here — these coins routinely swing
# 20-30% intraday with no signal in it. The trade-off: a wider stop means a
# real reversal costs more before it's caught.

STOP_LOSS_PCT = -35.0            # exit everything if down this much from entry
TRIM_1_GAIN_PCT = 100.0          # first take-profit trim point (+100%)
TRIM_1_SELL_PCT = 50.0           # sell half the position there
TRIM_2_GAIN_PCT = 300.0          # second trim point (+300% from entry)
TRIM_2_SELL_PCT = 50.0           # sell half of what's LEFT (25% of original)
TRAIL_ACTIVATE_GAIN_PCT = 50.0   # only start trailing once up this much
TRAIL_DRAWDOWN_PCT = 30.0        # exit remainder if it gives back this much from peak
STALL_HOURS = 6.0                # after this long, flat/negative momentum = exit


def momentum_score(token: dict) -> dict:
    """A 0-100 composite score from what's checkable in a DexScreener pair:
    short-vs-longer-horizon price acceleration, buy/sell pressure, and
    liquidity depth. Every component and its contribution is returned, never
    just a number — so 'why' is always inspectable.

    This describes RIGHT NOW. It has no memory of yesterday and no view of
    tomorrow.
    """
    components: list[dict] = []
    score = 0.0

    h1 = token.get("price_change_h1_pct")
    h6 = token.get("price_change_h6_pct")
    h24 = token.get("price_change_h24_pct")

    # Acceleration: is the SHORT window outpacing the longer one? That's
    # fresh buying, not just riding an old move. Worth more than raw size.
    if h1 is not None and h6 is not None:
        if h1 > 0 and h1 >= h6 / 6.0:   # h1 pace beats the h6 average pace
            pts = min(25.0, max(0.0, h1))
            score += pts
            components.append({"points": pts, "reason":
                              f"1h move ({h1:+.1f}%) is outpacing the 6h average "
                              f"pace — fresh buying, not a stale move"})
        elif h1 < 0:
            components.append({"points": 0.0, "reason":
                              f"1h move is negative ({h1:+.1f}%) — momentum has "
                              f"turned, not building"})
    if h24 is not None and h24 > 0:
        pts = min(15.0, h24 / 4.0)
        score += pts
        components.append({"points": pts, "reason": f"24h move {h24:+.1f}%"})

    buys_h1, sells_h1 = token.get("buys_h1") or 0, token.get("sells_h1") or 0
    total_h1 = buys_h1 + sells_h1
    if total_h1 >= 10:   # enough transactions to mean something
        ratio = buys_h1 / total_h1
        pts = max(0.0, (ratio - 0.5) * 60.0)   # 50/50 = 0pts, 100% buys = 30pts
        score += pts
        components.append({"points": pts, "reason":
                          f"1h buy/sell pressure: {buys_h1} buys vs {sells_h1} sells "
                          f"({ratio * 100:.0f}% buys)"})
    else:
        components.append({"points": 0.0, "reason":
                          f"too few 1h transactions ({total_h1}) to read pressure "
                          f"reliably"})

    liq = token.get("liquidity_usd") or 0.0
    if liq >= MIN_LIQUIDITY_FOR_ENTRY_USD:
        pts = min(20.0, liq / 10_000.0)
        score += pts
        components.append({"points": pts, "reason": f"liquidity ${liq:,.0f}"})
    else:
        components.append({"points": 0.0, "reason":
                          f"liquidity ${liq:,.0f} is below the "
                          f"${MIN_LIQUIDITY_FOR_ENTRY_USD:,.0f} entry floor"})

    vol = token.get("volume_24h_usd") or 0.0
    if liq > 0:
        ratio = vol / liq
        if 1.0 <= ratio <= 15.0:   # active but not wash-trading-shaped
            pts = 10.0
            score += pts
            components.append({"points": pts, "reason":
                              f"volume/liquidity ratio {ratio:.1f}x is in a "
                              f"healthy, active-but-not-suspicious range"})
        elif ratio > 15.0:
            components.append({"points": 0.0, "reason":
                              f"volume/liquidity ratio {ratio:.1f}x is high enough "
                              f"to look like wash trading, not organic demand"})

    return {"score": round(min(100.0, score), 1), "components": components}


@dataclass
class EntrySignal:
    enter: bool
    score: float
    reasons: list[str]
    risk_flags: list[dict]


def entry_signal(token: dict, mint_info: dict, *, now_ms: Optional[int] = None,
                 min_score: float = MIN_ENTRY_SCORE) -> EntrySignal:
    """Combine the risk screen (any red flag is an automatic no) with the
    momentum score against `min_score`. Never a buy call by itself — this
    tells you whether the tape and the structural facts support looking
    further, nothing more."""
    flags = memecoin.risk_flags(token, mint_info, now_ms=now_ms)
    reasons: list[str] = []
    if any(f["level"] == "red" for f in flags):
        return EntrySignal(enter=False, score=0.0,
                           reasons=["a red risk flag is present — never enter regardless of momentum"],
                           risk_flags=flags)

    m = momentum_score(token)
    reasons.extend(c["reason"] for c in m["components"] if c["points"] > 0)
    enter = m["score"] >= min_score
    if not enter:
        reasons.append(f"momentum score {m['score']:.0f} is below the "
                       f"{min_score:.0f} entry threshold")
    return EntrySignal(enter=enter, score=m["score"], reasons=reasons, risk_flags=flags)


@dataclass
class ExitSignal:
    exit: bool
    sell_pct: float   # % of the CURRENT holding to sell, if exit is True
    reason: str


def exit_signal(*, entry_price_usd: float, current_price_usd: float,
                peak_price_usd: float, hours_held: float,
                token: Optional[dict] = None,
                already_trimmed_1: bool = False,
                already_trimmed_2: bool = False,
                stop_loss_pct: float = STOP_LOSS_PCT,
                trim_1_gain_pct: float = TRIM_1_GAIN_PCT,
                trim_1_sell_pct: float = TRIM_1_SELL_PCT,
                trim_2_gain_pct: float = TRIM_2_GAIN_PCT,
                trim_2_sell_pct: float = TRIM_2_SELL_PCT,
                trail_activate_gain_pct: float = TRAIL_ACTIVATE_GAIN_PCT,
                trail_drawdown_pct: float = TRAIL_DRAWDOWN_PCT,
                stall_hours: float = STALL_HOURS) -> ExitSignal:
    """What to do about a live position, checked in order of urgency:
    stop-loss first (capital protection outranks everything), then take-
    profit trims, then a trailing stop protecting gains already made, then a
    momentum-stall exit if the tape has gone quiet. Returns the single most
    urgent applicable action — never advice to hold and also do something
    else. `already_trimmed_*` prevents re-firing a trim that already happened
    (the caller tracks that per-position; this function is stateless).

    All thresholds default to the swing-trade constants above; `scalp_exit_signal`
    calls this with the tighter SCALP_* constants instead — one set of logic,
    two threshold profiles, so they can never silently drift apart."""
    if entry_price_usd <= 0:
        raise memecoin.MemecoinError("invalid entry price")

    gain_pct = (current_price_usd - entry_price_usd) / entry_price_usd * 100.0

    if gain_pct <= stop_loss_pct:
        return ExitSignal(exit=True, sell_pct=100.0,
                          reason=f"stop-loss: down {gain_pct:.1f}% from entry "
                                f"(limit {stop_loss_pct:.0f}%) — capital protection first")

    if gain_pct >= trim_2_gain_pct and not already_trimmed_2:
        return ExitSignal(exit=True, sell_pct=trim_2_sell_pct,
                          reason=f"take-profit: up {gain_pct:.1f}% (past "
                                f"+{trim_2_gain_pct:.0f}%) — trim {trim_2_sell_pct:.0f}% "
                                f"of what's left, let the rest ride")

    if gain_pct >= trim_1_gain_pct and not already_trimmed_1:
        return ExitSignal(exit=True, sell_pct=trim_1_sell_pct,
                          reason=f"take-profit: up {gain_pct:.1f}% (past "
                                f"+{trim_1_gain_pct:.0f}%) — trim {trim_1_sell_pct:.0f}% "
                                f"and de-risk the trade")

    peak_gain_pct = (peak_price_usd - entry_price_usd) / entry_price_usd * 100.0
    if peak_gain_pct >= trail_activate_gain_pct and peak_price_usd > 0:
        drawdown_pct = (peak_price_usd - current_price_usd) / peak_price_usd * 100.0
        if drawdown_pct >= trail_drawdown_pct:
            return ExitSignal(exit=True, sell_pct=100.0,
                              reason=f"trailing stop: peaked at +{peak_gain_pct:.1f}%, "
                                    f"now down {drawdown_pct:.1f}% from that peak "
                                    f"(limit {trail_drawdown_pct:.0f}%) — protect the gain")

    if hours_held >= stall_hours:
        h1 = token.get("price_change_h1_pct") if token is not None else None
        if h1 is not None:
            buys_h1 = token.get("buys_h1") or 0
            sells_h1 = token.get("sells_h1") or 0
            if h1 <= 0 and sells_h1 > buys_h1:
                return ExitSignal(exit=True, sell_pct=100.0,
                                  reason=f"momentum stalled: held {hours_held:.1f}h, 1h "
                                        f"move {h1:+.1f}%, sell pressure exceeds buy "
                                        f"pressure — the move this was betting on is over")
        else:
            # No buy/sell-pressure data (e.g. a brand-new pump.fun coin with no
            # DexScreener history yet) — for a scalp, holding past the deadline
            # with nothing to show for it IS the stall signal, full stop.
            return ExitSignal(exit=True, sell_pct=100.0,
                              reason=f"held {hours_held:.1f}h past the stall deadline "
                                    f"({stall_hours:.2f}h) with no move — no data to "
                                    f"justify holding longer")

    return ExitSignal(exit=False, sell_pct=0.0,
                      reason=f"no exit rule triggered ({gain_pct:+.1f}% from entry)")


# --- scalp profile: same rules, much tighter thresholds --------------------
# A scalp on a brand-new, ultra-low-cap coin is a different bet than a swing
# position: the edge (if any) decays in minutes, not days, so gains must be
# taken fast and a loser cut fast. These are deliberately much tighter than
# the swing constants above.

SCALP_STOP_LOSS_PCT = -15.0
SCALP_TRIM_1_GAIN_PCT = 15.0
SCALP_TRIM_1_SELL_PCT = 50.0
SCALP_TRIM_2_GAIN_PCT = 40.0
SCALP_TRIM_2_SELL_PCT = 50.0
SCALP_TRAIL_ACTIVATE_GAIN_PCT = 20.0
SCALP_TRAIL_DRAWDOWN_PCT = 15.0
SCALP_STALL_HOURS = 0.5   # 30 minutes


def scalp_exit_signal(**kwargs) -> ExitSignal:
    """exit_signal() with the scalp threshold profile. Same priority order,
    same statelessness — only the numbers change."""
    kwargs.setdefault("stop_loss_pct", SCALP_STOP_LOSS_PCT)
    kwargs.setdefault("trim_1_gain_pct", SCALP_TRIM_1_GAIN_PCT)
    kwargs.setdefault("trim_1_sell_pct", SCALP_TRIM_1_SELL_PCT)
    kwargs.setdefault("trim_2_gain_pct", SCALP_TRIM_2_GAIN_PCT)
    kwargs.setdefault("trim_2_sell_pct", SCALP_TRIM_2_SELL_PCT)
    kwargs.setdefault("trail_activate_gain_pct", SCALP_TRAIL_ACTIVATE_GAIN_PCT)
    kwargs.setdefault("trail_drawdown_pct", SCALP_TRAIL_DRAWDOWN_PCT)
    kwargs.setdefault("stall_hours", SCALP_STALL_HOURS)
    return exit_signal(**kwargs)


# --- pump.fun-native entry: scoring a coin with no DexScreener history yet --
#
# A coin fresh off pump.fun's creation feed usually has no price-change or
# buy/sell transaction history the way a DexScreener pair does — there is no
# comparable liquidity figure either (a bonding curve is not a liquidity
# pool). What IS checkable: how fresh it is, and how much SOL has been
# raised on the curve so far as a rough proxy for real buying (which can
# also just mean the creator/insiders bought — this is a much weaker signal
# than DexScreener's momentum_score above, by necessity).

SCALP_MIN_ENTRY_SCORE = 50.0


def pumpfun_momentum_score(coin: dict, *, buyer_stats: Optional[dict] = None) -> dict:
    """Freshness + buyer diversity + bonding-curve progress, 40/40/20.

    Buyer diversity (distinct wallets buying, from PumpPortal's free trade
    stream) is weighted as heavily as freshness deliberately: SOL raised
    fast from ONE wallet — or a handful funded from the same source in the
    same block — is the textbook bundled/insider-launch pattern real
    pump.fun scalpers watch for, not organic demand. Raw SOL-raised alone
    can't tell those apart; buyer count can. Missing buyer_stats (no
    PumpPortal connection, or a mint too new to have data yet) just drops
    that component to 0 — never blocks scoring on the other two."""
    components: list[dict] = []
    score = 0.0

    created_at_ms = coin.get("created_at_ms")
    if created_at_ms:
        import time as _time
        age_min = max(0.0, (_time.time() * 1000 - created_at_ms) / 60_000.0)
        pts = max(0.0, 40.0 * (1 - min(age_min, 30.0) / 30.0))
        score += pts
        components.append({"points": pts, "reason": f"created {age_min:.1f} min ago"})
    else:
        components.append({"points": 0.0, "reason": "no creation timestamp available"})

    if buyer_stats is not None:
        unique = buyer_stats.get("unique_buyers") or 0
        pts = min(40.0, unique * 4.0)
        score += pts
        components.append({"points": pts, "reason":
                          f"{unique} distinct wallet(s) have bought so far (PumpPortal)"})
    else:
        components.append({"points": 0.0, "reason":
                          "no buyer-diversity data yet (PumpPortal not connected, or too new)"})

    sol_raised = coin.get("sol_raised")
    if sol_raised is not None:
        pts = min(20.0, max(0.0, sol_raised) * 1.0)
        score += pts
        components.append({"points": pts, "reason":
                          f"{sol_raised:.2f} SOL raised on the bonding curve so far"})
    else:
        components.append({"points": 0.0, "reason": "no bonding-curve progress data available"})

    return {"score": round(min(100.0, score), 1), "components": components}


def pumpfun_entry_signal(coin: dict, mint_info: dict, *,
                         min_score: float = SCALP_MIN_ENTRY_SCORE,
                         buyer_stats: Optional[dict] = None) -> EntrySignal:
    """The pump.fun-native equivalent of entry_signal(): mint/freeze
    authority is still checked (memecoin.pumpfun_risk_flags — the only
    universally-applicable structural check this early); everything past
    that is a much thinner momentum read than DexScreener's, by necessity."""
    from hf_trading_bot import memecoin

    flags = memecoin.pumpfun_risk_flags(coin, mint_info)
    if any(f["level"] == "red" for f in flags):
        return EntrySignal(enter=False, score=0.0,
                           reasons=["a red risk flag is present — never enter regardless "
                                   "of momentum"],
                           risk_flags=flags)
    m = pumpfun_momentum_score(coin, buyer_stats=buyer_stats)
    reasons = [c["reason"] for c in m["components"] if c["points"] > 0]
    enter = m["score"] >= min_score
    if not enter:
        reasons.append(f"momentum score {m['score']:.0f} is below the {min_score:.0f} "
                       f"entry threshold")
    return EntrySignal(enter=enter, score=m["score"], reasons=reasons, risk_flags=flags)
