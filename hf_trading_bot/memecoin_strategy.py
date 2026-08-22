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
                already_trimmed_2: bool = False) -> ExitSignal:
    """What to do about a live position, checked in order of urgency:
    stop-loss first (capital protection outranks everything), then take-
    profit trims, then a trailing stop protecting gains already made, then a
    momentum-stall exit if the tape has gone quiet. Returns the single most
    urgent applicable action — never advice to hold and also do something
    else. `already_trimmed_*` prevents re-firing a trim that already happened
    (the caller tracks that per-position; this function is stateless)."""
    if entry_price_usd <= 0:
        raise memecoin.MemecoinError("invalid entry price")

    gain_pct = (current_price_usd - entry_price_usd) / entry_price_usd * 100.0

    if gain_pct <= STOP_LOSS_PCT:
        return ExitSignal(exit=True, sell_pct=100.0,
                          reason=f"stop-loss: down {gain_pct:.1f}% from entry "
                                f"(limit {STOP_LOSS_PCT:.0f}%) — capital protection first")

    if gain_pct >= TRIM_2_GAIN_PCT and not already_trimmed_2:
        return ExitSignal(exit=True, sell_pct=TRIM_2_SELL_PCT,
                          reason=f"take-profit: up {gain_pct:.1f}% (past "
                                f"+{TRIM_2_GAIN_PCT:.0f}%) — trim {TRIM_2_SELL_PCT:.0f}% "
                                f"of what's left, let the rest ride")

    if gain_pct >= TRIM_1_GAIN_PCT and not already_trimmed_1:
        return ExitSignal(exit=True, sell_pct=TRIM_1_SELL_PCT,
                          reason=f"take-profit: up {gain_pct:.1f}% (past "
                                f"+{TRIM_1_GAIN_PCT:.0f}%) — trim {TRIM_1_SELL_PCT:.0f}% "
                                f"and de-risk the trade")

    peak_gain_pct = (peak_price_usd - entry_price_usd) / entry_price_usd * 100.0
    if peak_gain_pct >= TRAIL_ACTIVATE_GAIN_PCT and peak_price_usd > 0:
        drawdown_pct = (peak_price_usd - current_price_usd) / peak_price_usd * 100.0
        if drawdown_pct >= TRAIL_DRAWDOWN_PCT:
            return ExitSignal(exit=True, sell_pct=100.0,
                              reason=f"trailing stop: peaked at +{peak_gain_pct:.1f}%, "
                                    f"now down {drawdown_pct:.1f}% from that peak "
                                    f"(limit {TRAIL_DRAWDOWN_PCT:.0f}%) — protect the gain")

    if hours_held >= STALL_HOURS and token is not None:
        h1 = token.get("price_change_h1_pct")
        buys_h1, sells_h1 = token.get("buys_h1") or 0, token.get("sells_h1") or 0
        if h1 is not None and h1 <= 0 and sells_h1 > buys_h1:
            return ExitSignal(exit=True, sell_pct=100.0,
                              reason=f"momentum stalled: held {hours_held:.1f}h, 1h "
                                    f"move {h1:+.1f}%, sell pressure exceeds buy "
                                    f"pressure — the move this was betting on is over")

    return ExitSignal(exit=False, sell_pct=0.0,
                      reason=f"no exit rule triggered ({gain_pct:+.1f}% from entry)")
