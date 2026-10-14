"""Paper-track a third-party allocation signal against doing nothing.

Waldo follows "Prof. Adam ~ Crypto Investing" (a paid Discord), which publishes a
target allocation and tells subscribers to "check in every day, signals can change
frequently". This module answers the only question that matters: after the real
cost of trading, does following it beat not following it?

THE COST THAT DECIDES IT. Robinhood crypto quotes carry a ~1.89% bid/ask spread
(measured 2026-10-09: BTC 1.87%, SUI 1.91%, SOL 1.91%, NEAR 1.94%, against
0.00-0.02% on an order-book venue). Every rebalance pays half the spread on the
way out and half on the way in, so turnover is charged at SPREAD_PCT of notional
traded. A signal must beat buy-and-hold by more than that drag to be worth
anything, however good its calls are.

THREE BENCHMARKS, all necessary:
  - BTC buy and hold: the sector's beta. Beating the alts is not an achievement if
    BTC did it too.
  - FROZEN sleeve: a paper allocation on day one, never touched. This isolates the
    value of the SIGNAL from the value of the ASSETS he already picked.
  - AS FILLED: the real sleeve, at the prices the broker actually charged, loaded
    from data/signals/position.json. This is the one that decides anything, because
    it is his money. The entry spread is already inside those fill prices, so the
    code must never charge it again -- see Position and actual().

Honest limits, stated because they bound every number this produces:
  - n is tiny. One signal is not a track record. Treat anything under ~20
    observations as a log, not a result, and say so when reporting.
  - Signals are recorded when Waldo forwards them, so a missed forward looks like
    "no change". The log records `source` for exactly this reason.
  - Prices are marks at log time, not fills. Real fills land at bid or ask, which
    is why SPREAD_PCT is charged separately rather than folded into the mark.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

LOG = Path("data/signals/adam.jsonl")
POSITION = Path("data/signals/position.json")
LEDGER = Path("data/signals/ledger.jsonl")

# Round-trip cost of moving $1 of notional on Robinhood crypto. Measured, not assumed.
SPREAD_PCT = 0.0189

# Below this, a rebalance costs more than the drift it corrects.
MIN_TRADE_USD = 10.0


def _ts(at: str) -> datetime:
    """Parse an ISO8601 stamp into an instant, for ORDERING.

    Sorting the raw strings is wrong the moment two stamps carry different UTC
    offsets. "2026-10-09T21:00:00Z" is 17:00 ET -- EARLIER than
    "2026-10-09T20:00:00-04:00" -- yet it sorts after it, so the log would be
    replayed out of order and every turnover figure computed between the wrong
    pair. The real log happens to be all -04:00 today, which is exactly why this
    stayed invisible: it stops being true on 2026-11-01 when ET moves to -05:00,
    and the tests already write "Z". Compare instants, never text.

    A stamp with no offset is refused rather than assumed to be local: guessing
    the zone is how a one-hour error becomes a silent one.
    """
    d = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"{at!r} carries no UTC offset -- an instant without a "
                         f"zone cannot be ordered against one that has it")
    return d


@dataclass
class Signal:
    """One published allocation. `weights` are fractions of the whole sleeve,
    CASH included -- Adam's posts sum to 100% with cash as a named slice."""
    at: str                      # ISO8601, when the signal was PUBLISHED
    weights: dict                # {"BTC": 0.318, ..., "CASH": 0.318}
    prices: dict                 # mark price per asset at log time
    source: str = "discord"      # how it reached us; "missed" marks a known gap
    note: str = ""

    def validate(self) -> None:
        _ts(self.at)                      # refuse an unorderable stamp at the door
        s = sum(self.weights.values())
        if abs(s - 1.0) > 0.005:
            raise ValueError(f"weights sum to {s:.4f}, not 1.0 -- cash slice missing?")
        for k, w in self.weights.items():
            if w < 0:
                raise ValueError(f"{k} is weighted {w:.4f}; a published long-only "
                                 f"allocation has no negative slice")
            if k != "CASH" and k not in self.prices:
                raise ValueError(f"no price for {k}")
        for a, px in self.prices.items():
            # A zero or negative price is not a data gap, it is a data error, and
            # it divides: follow() sizes units as delta/price and frozen() as
            # notional/price. Both would produce an infinity that then propagates
            # through every arm of the report as a plausible-looking number.
            if px <= 0:
                raise ValueError(f"{a} priced at {px} -- a price must be positive")


def log_signal(sig: Signal, path: Path = LOG) -> None:
    """Append one signal. Refuses a stamp the log already holds.

    Double-logging is the likeliest real mistake here, because signals arrive as
    screenshots and get transcribed by hand. It is also the one that lies in the
    useful direction: a repeated post reads as a no-change transition, which
    lowers mean turnover and so UNDERSTATES what following costs. Measured on
    the live log, one duplicate moved the implied drag from 11.7%/yr to 9.4%/yr
    -- it made the subscription look $30/yr cheaper than it is, with no error
    anywhere. Re-publishing an identical allocation is real and must be logged;
    logging the same publication twice is not.
    """
    sig.validate()
    existing = {s.at for s in read_signals(path)}
    if sig.at in existing:
        raise ValueError(f"the log already holds a signal stamped {sig.at}. A "
                         f"repeated post is real, a repeated LOG is not -- it "
                         f"shows up as a free no-change and understates turnover.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(sig)) + "\n")


def read_signals(path: Path = LOG) -> list[Signal]:
    """Load the log, ordered by instant, every row validated.

    Validation used to happen only on the way in, which left the file itself
    unchecked: a hand-edited row with a broken weight sum flowed straight into
    the arithmetic and produced a confident number. The log IS hand-edited --
    that is how the missing 2026-10-11 signal was recovered -- so the read is
    the only place a check reliably runs.
    """
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            sig = Signal(**json.loads(line))
            sig.validate()
        except Exception as e:
            raise ValueError(f"{path}:{i} is not a usable signal -- {e}") from e
        out.append(sig)
    seen: dict[str, int] = {}
    for s in out:
        seen[s.at] = seen.get(s.at, 0) + 1
    dupes = sorted(a for a, c in seen.items() if c > 1)
    if dupes:
        raise ValueError(f"{path} holds duplicate stamps {dupes} -- a repeated log "
                         f"reads as a free no-change and understates turnover")
    return sorted(out, key=lambda s: _ts(s.at))


def _value(units: dict, cash: float, prices: dict) -> float:
    """Value a holding. STRICT: an unpriceable leg raises rather than vanishing.

    This used to skip what it could not price (`if a in prices`), which turned a
    missing price into a sleeve quietly short by that holding's entire value --
    no error, no flag, just a wrong number that looked fine. Three separate
    call sites hit that hole in two days. A loud failure is always preferable:
    it gets fixed in minutes, a quiet one gets believed.
    """
    missing = [a for a, u in units.items() if a not in prices and u != 0.0]
    if missing:
        raise ValueError(f"no price for {', '.join(sorted(missing))} -- cannot value the holding")
    return cash + sum(u * prices[a] for a, u in units.items() if a in prices)


def follow(signals: list[Signal], start_usd: float,
           spread: float = SPREAD_PCT, min_trade: float = MIN_TRADE_USD) -> dict:
    """Walk the signal history, rebalancing to each new target and paying spread.

    COST CONVENTION, and it decides the answer. SPREAD_PCT is the ROUND TRIP --
    the full bid-to-ask distance. A single leg crosses only half of it: a buy
    lifts the ask (mid + spread/2), a sell hits the bid (mid - spread/2). So each
    leg is charged `spread / 2`, which is what hold() and frozen() already charge
    on entry. Rotating $100 from SOL into BTC is two legs and therefore costs one
    full SPREAD_PCT of $100 -- half on the way out, half on the way in.

    This was wrong until 2026-10-11: every leg was charged the full SPREAD_PCT,
    so follow() paid double what its own benchmarks paid for identical turnover.
    The error ran against the signal, which is the direction that flattered the
    argument I had already made against following it. Waldo's real fills settle
    the convention: they landed 0.99% above the mark, half of the 1.89% round
    trip, exactly as one leg should.

    THE COST COMPOUNDS, and that was wrong too. The accumulated spread used to
    be parked as negative cash and subtracted only after the walk, so it never
    participated in any price move. hold() subtracted its entry cost BEFORE
    buying units, so hold()'s cost did compound. On an identical 100%-BTC
    position through a doubling, follow() therefore came out $9.45 ahead of
    hold() -- the paid signal's arm escaping a toll its own benchmark paid.
    Spread is charged to cash at the moment of the fill, so the position bought
    is smaller and every subsequent return is earned on the smaller position,
    which is what actually happens to the money.

    Returns the final value plus the running cost, so the drag is never hidden
    inside the return.
    """
    if not signals:
        return {"value": start_usd, "cost": 0.0, "trades": 0, "n": 0,
                "units": {}, "cash": start_usd, "shortfall": 0.0}
    units: dict[str, float] = {}
    cash = start_usd
    cost = 0.0
    trades = 0
    shortfall = 0.0
    px: dict[str, float] = {}        # carried forward, so a held leg is always priceable
    for sig in signals:
        px.update(sig.prices)
        total = _value(units, cash, px)
        # An asset ABSENT from a published set is at zero, not unchanged. The
        # weights sum to 1.0, so there is no room left for a holding they omit.
        # Iterating sig.weights alone left such a holding untouched AND then
        # unpriced, and the follower's sleeve was reported short by its whole
        # value with no error raised.
        deltas = {}
        for asset in sorted(set(sig.weights) | set(units)):
            if asset == "CASH":
                continue
            d = total * sig.weights.get(asset, 0.0) - units.get(asset, 0.0) * px[asset]
            if abs(d) >= min_trade:       # below this, the spread exceeds the drift
                deltas[asset] = d
        # SELLS FIRST, then buys, and sorted within each -- the order a real
        # rebalance runs in, because a sell is what funds the buy. Iterating a
        # set() was also non-deterministic across runs: set order over strings
        # depends on PYTHONHASHSEED, and a benchmark that moves between runs
        # cannot be argued with.
        for asset in sorted(deltas, key=lambda a: (deltas[a] > 0, a)):
            delta = deltas[asset]
            if delta > 0:
                # A buy cannot spend money the sleeve does not have. Sizing
                # targets on the pre-cost mark and charging the spread afterwards
                # let cash go NEGATIVE: measured on a 100%-invested sleeve, the
                # arm ended holding $990.55 of BTC against $971.74 of sleeve --
                # 1.01x leverage, silently, on a signal whose own posts read
                # "BTC Leverage Impermissible". A broker rejects that order; so
                # does this. Spend at most the cash on hand, cost included:
                #   delta + delta*(spread/2) <= cash
                afford = max(cash, 0.0) / (1.0 + spread / 2)
                if afford < delta:
                    shortfall += delta - afford   # reported, never hidden
                    delta = afford
                if delta < min_trade:
                    continue
            leg = abs(delta) * (spread / 2)       # one leg crosses half the spread
            cost += leg
            trades += 1
            units[asset] = units.get(asset, 0.0) + delta / px[asset]
            # Charged NOW, not after the walk. Deferring it left every
            # intermediate `total` overstated by the spread paid so far, so the
            # follower rebalanced a sleeve still holding money it had spent --
            # a small overstatement of the arm under test, compounding per post.
            cash -= delta + leg
        for asset in [a for a, u in units.items() if abs(u) < 1e-15]:
            del units[asset]          # a fully exited leg leaves the book
    if cash < -1e-9:
        raise ValueError(f"the follow arm ended with ${cash:,.2f} of cash -- it "
                         f"borrowed to hold its targets, which is not what the "
                         f"signal says to do")
    return {"value": _value(units, cash, px), "cost": cost, "trades": trades,
            "n": len(signals), "units": units, "cash": cash,
            "shortfall": shortfall}


def hold(asset: str, signals: list[Signal], start_usd: float,
         prices: dict | None = None) -> dict:
    """Buy once at the earliest price the log holds, never trade again.

    Pays half a spread on entry only -- the honest comparison, since the
    follower pays it too.

    Both ends are looked up across the WHOLE log rather than off signals[0] and
    signals[-1] alone. Indexing the last signal's price dict is the same bug that
    killed the control arm on 2026-10-13: the day Adam drops BTC from his book,
    the BTC buy-and-hold benchmark stops being quoted in his posts and the arm
    raises KeyError -- demonstrated, not supposed. A buy-and-hold benchmark must
    not depend on the signal continuing to mention the asset; that is the entire
    point of it. `prices` supplies live quotes, which is better than any mark.
    """
    if not signals:
        return {"value": start_usd}
    first = next((s.prices[asset] for s in signals if asset in s.prices), None)
    if first is None:
        raise ValueError(f"{asset} is never priced in the log -- cannot hold it")
    px, as_of = latest_prices(signals, prices)
    # Deploying C of cash acquires C/(1 + spread/2) of notional, because the buy
    # lifts the ask. NOT (C - C*spread/2): that charges half a spread on the
    # whole outlay, the spread included. See the note on the cost convention in
    # follow() -- one rule for one question, or the arms drift apart.
    notional = start_usd / (1.0 + SPREAD_PCT / 2)
    units = notional / first
    return {"value": units * px[asset], "cost": start_usd - notional,
            "units": units, "entry": first, "as_of": as_of[asset]}


def latest_prices(signals: list[Signal], extra: dict | None = None) -> tuple[dict, dict]:
    """The most recent price the log holds for EVERY asset it has ever priced.

    The control arm keeps holding what it bought, so once a signal drops an
    asset the latest signal's price dict no longer covers the whole frozen
    sleeve and marking it raises. This walks the log newest-first and carries
    forward the last price seen for each asset.

    Returns (prices, as_of). `as_of` names the signal each price came from, so a
    caller can say which legs are being marked on a stale row instead of
    presenting a mixed-date valuation as though it were current. `extra`
    overrides with live quotes, which is always preferable when they are to hand.
    """
    prices: dict[str, float] = {}
    as_of: dict[str, str] = {}
    for sig in reversed(signals):          # newest first
        for a, px in sig.prices.items():
            if a not in prices:
                prices[a], as_of[a] = px, sig.at
    for a, px in (extra or {}).items():
        prices[a], as_of[a] = px, "live"
    return prices, as_of


def frozen(weights: dict, signals: list[Signal], start_usd: float,
           prices: dict | None = None) -> dict:
    """A day-one allocation, bought once and never rebalanced.

    Marked with `prices` when given, otherwise with the last price the log holds
    for each asset. It must NOT be marked on the latest signal's price dict
    alone: once a signal drops an asset, _value() would quietly skip the leg it
    cannot price and report a sleeve short by that holding's entire value --
    a silent undervaluation, which is worse than the raise it replaces.
    """
    if not signals:
        return {"value": start_usd}
    p0 = signals[0].prices
    px = prices if prices is not None else latest_prices(signals)[0]
    unpriced = [a for a in weights if a != "CASH" and a not in p0]
    if unpriced:
        raise ValueError(f"the first signal does not price {', '.join(sorted(unpriced))} "
                         f"-- a frozen sleeve has to be bought at day-one prices")
    units = {a: (start_usd * w) / p0[a] for a, w in weights.items() if a != "CASH"}
    missing = [a for a in units if a not in px]
    if missing:
        raise ValueError(f"no price for {', '.join(sorted(missing))} -- cannot mark the frozen sleeve")
    entry_cost = start_usd * (1 - weights.get("CASH", 0.0)) * (SPREAD_PCT / 2)
    cash = start_usd * weights.get("CASH", 0.0) - entry_cost
    return {"value": _value(units, cash, px), "cost": entry_cost}


def naive_weights(signals: list[Signal]) -> dict:
    """Equal weight across whatever the FIRST signal named, at its own cash level.

    The benchmark that needs no foresight. It keeps Adam's asset selection and
    his risk level, and throws away only his weighting -- so beating it is the
    narrowest honest claim the signal can make. A "hold the legs that never
    moved" benchmark would be hindsight: which legs those are is only knowable
    after watching them not move.
    """
    if not signals:
        return {}
    risky = [a for a in signals[0].weights if a != "CASH"]
    cash = signals[0].weights.get("CASH", 0.0)
    return {**{a: (1.0 - cash) / len(risky) for a in risky}, "CASH": cash}


@dataclass
class Position:
    """The sleeve as actually filled, taken from broker order records.

    `basis` is the dollars the broker really took, so the entry spread is ALREADY
    inside it. Nothing here may be charged SPREAD_PCT a second time -- doing so
    would understate his real starting point and flatter every alternative.

    `cash` is the uninvested remainder of the sleeve, not the whole account: this
    tracker's universe is the crypto sleeve he designated, nothing else.
    """
    at: str                                        # ISO8601 of the last fill
    units: dict                                    # asset -> units held
    basis: dict                                    # asset -> USD actually paid
    cash: float                                    # uninvested sleeve cash
    orders: dict = field(default_factory=dict)     # asset -> broker order id
    note: str = ""
    committed: float | None = None   # capital first put in; None means "this IS entry"
    realized: float = 0.0            # cumulative realised P&L from closed units

    @property
    def invested(self) -> float:
        return sum(self.basis.values())

    @property
    def capital(self) -> float:
        """Capital originally committed to the sleeve -- the denominator for every
        return this module prints.

        At entry this is simply paid-plus-cash. After trading it must NOT be
        recomputed that way: realised losses shrink paid-plus-cash, which would
        quietly shrink the denominator and inflate the reported return by exactly
        the amount that was lost. `committed` pins it to the money he actually
        put in."""
        if self.committed is not None:
            return self.committed
        return self.invested + self.cash

    def fill_price(self, asset: str) -> float:
        return self.basis[asset] / self.units[asset]

    def weights(self) -> dict:
        """Realised weights at entry, cash included, so they are comparable to a
        Signal's published weights."""
        cap = self.capital
        w = {a: b / cap for a, b in self.basis.items()}
        w["CASH"] = self.cash / cap
        return w

    def validate(self) -> None:
        for a, u in self.units.items():
            if a not in self.basis:
                raise ValueError(f"no cost basis for {a} -- fill not recorded")
            if u <= 0:
                raise ValueError(f"{a} holds {u} units; an opening position needs a fill")
        if self.cash < 0:
            raise ValueError("sleeve cash is negative -- the sleeve was overspent")


def load_position(path: Path = POSITION) -> Position | None:
    if not path.exists():
        return None
    pos = Position(**json.loads(path.read_text()))
    pos.validate()
    return pos


def save_position(pos: Position, path: Path = POSITION) -> None:
    pos.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(pos), indent=2) + "\n")


def value_position(pos: Position, prices: dict) -> dict:
    """Mark the real sleeve. Refuses to price a position it cannot price in full,
    because a silently missing asset reads as a loss.

    The check lives in _value and is NOT duplicated here. It used to be, with a
    subtly different rule -- this one fired on any named asset, _value's on any
    asset actually held -- so a fully exited leg left at zero units raised from
    one path and valued cleanly from the other. Two rules for one question is
    how they drift apart.
    """
    mark = _value(pos.units, pos.cash, prices)
    cap = pos.capital
    if cap <= 0:
        raise ValueError(f"sleeve capital is ${cap:,.2f}; a return needs a "
                         f"positive denominator")
    return {"value": mark, "capital": cap, "pnl": mark - cap,
            "ret": mark / cap - 1}


def actual(pos: Position, signals: list[Signal]) -> dict:
    """The real sleeve, untouched since entry, marked at the latest signal's prices.

    This is what following the signal has to beat. No spread is charged: he has
    already paid it, and the market kept the money.
    """
    if not signals:
        raise ValueError("need at least one signal to mark the position against")
    return value_position(pos, signals[-1].prices)


def entry_premium(pos: Position, signal: Signal) -> dict:
    """How far the real fills landed above the marks logged with a signal.

    This is the only place the 1.89% SPREAD_PCT assumption meets evidence, and it
    is NOT a clean measurement of the spread: the marks are logged minutes before
    the fills, so price drift is mixed in and cannot be separated out. Read it as
    a consistency check. A weighted premium near zero would mean the spread
    assumption is too harsh; one near a full SPREAD_PCT would mean it is far too
    kind; one near SPREAD_PCT/2 means the model is roughly right about entry.
    """
    per = {}
    paid = at_mark = 0.0
    for a, u in pos.units.items():
        if a not in signal.prices:
            continue
        per[a] = pos.fill_price(a) / signal.prices[a] - 1
        paid += pos.basis[a]
        at_mark += u * signal.prices[a]
    if at_mark <= 0:
        raise ValueError("no overlap between the position and the signal's prices")
    weighted = paid / at_mark - 1
    return {"per_asset": per, "weighted": weighted,
            "implied_round_trip": weighted * 2,
            "assumed_round_trip": SPREAD_PCT,
            "paid": paid, "at_mark": at_mark, "excess": paid - at_mark}


@dataclass
class Trade:
    """One real fill, from the broker's order record.

    `notional` is the USD the broker moved, fees included. `basis_delta` is the
    signed change in cost basis -- the notional on a buy, minus the DISPOSED
    basis on a sell. The disposed figure comes from the broker's own lot
    accounting rather than being recomputed here, because guessing a disposal
    method and quietly getting it wrong would misstate realised P&L.
    """
    at: str
    asset: str
    side: str                 # "buy" | "sell"
    units: float
    notional: float
    basis_delta: float
    order_id: str = ""
    note: str = ""

    @property
    def realized(self) -> float:
        """Only a sale closes units, so only a sale realises anything."""
        return 0.0 if self.side == "buy" else self.notional + self.basis_delta

    def validate(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side is {self.side!r}, not buy or sell")
        if self.units <= 0 or self.notional <= 0:
            raise ValueError("units and notional are magnitudes; both must be positive")
        if self.side == "buy" and self.basis_delta <= 0:
            raise ValueError("a buy adds cost basis")
        if self.side == "sell" and self.basis_delta >= 0:
            raise ValueError("a sale removes cost basis")


def log_trade(t: Trade, path: Path = LEDGER) -> None:
    t.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(t)) + "\n")


def read_trades(path: Path = LEDGER) -> list[Trade]:
    if not path.exists():
        return []
    out = [Trade(**json.loads(ln)) for ln in path.read_text().splitlines() if ln.strip()]
    return sorted(out, key=lambda t: t.at)


def apply_trades(pos: Position, trades: list[Trade]) -> Position:
    """Replay real fills onto the opening position to get the live sleeve.

    The opening Position stays untouched on disk: it is the control arm of the
    experiment -- what he would hold had he never rebalanced. Overwriting it
    with the live state would delete the only thing the signal has to beat.
    """
    units = dict(pos.units)
    basis = dict(pos.basis)
    cash = pos.cash
    realized = pos.realized
    for t in trades:
        sign = 1 if t.side == "buy" else -1
        units[t.asset] = units.get(t.asset, 0.0) + sign * t.units
        basis[t.asset] = basis.get(t.asset, 0.0) + t.basis_delta
        cash -= sign * t.notional
        realized += t.realized
        if units[t.asset] < -1e-12:
            raise ValueError(f"{t.asset} sold below zero at {t.at} -- ledger is wrong")
    # A fully exited asset leaves the sleeve. Keeping a zero-unit entry would
    # oblige every future report to carry a price for something no longer held,
    # so the day a dropped coin stops being quoted the whole report fails. A
    # leftover basis on zero units means the disposal was mis-recorded, which is
    # worth refusing rather than silently carrying.
    for a in [a for a, u in units.items() if abs(u) < 1e-12]:
        if abs(basis[a]) > 0.005:
            raise ValueError(f"{a} is fully sold but ${basis[a]:,.2f} of basis "
                             f"remains -- the disposal was mis-recorded")
        del units[a]
        del basis[a]
    # Merge, do not replace: building the dict from `trades` alone threw away the
    # opening order id of every asset that has not traded since, so the live
    # sleeve lost the broker provenance for SUI and NEAR -- the two legs whose
    # fills have never been revisited and are therefore the ones most likely to
    # need looking up.
    ids = {**pos.orders, **{t.asset: t.order_id for t in trades if t.order_id}}
    return Position(at=trades[-1].at if trades else pos.at,
                    units=units, basis=basis, cash=cash,
                    orders={a: i for a, i in ids.items() if a in units},
                    note="derived by replaying the ledger onto the opening position",
                    committed=pos.capital, realized=realized)


def live_position(pos_path: Path = POSITION, led_path: Path = LEDGER) -> Position | None:
    """The sleeve as it stands now: opening fills plus every trade since."""
    opening = load_position(pos_path)
    if opening is None:
        return None
    trades = read_trades(led_path)
    return apply_trades(opening, trades) if trades else opening


def opening_report(pos: Position, signals: list[Signal]) -> str:
    """What can honestly be said when only one signal has been logged: where the
    real money started, and what entry cost. No comparison -- there is nothing
    yet to compare against."""
    v = actual(pos, signals)
    prem = entry_premium(pos, signals[0])
    lines = [
        f"Opening position recorded {pos.at[:16].replace('T', ' ')} "
        f"-- ${pos.capital:,.2f} of sleeve capital.",
        "",
        f"{'asset':<6}{'units':>14}{'fill':>12}{'paid':>11}{'vs mark':>10}",
    ]
    for a in sorted(pos.basis, key=lambda x: -pos.basis[x]):
        lines.append(f"{a:<6}{pos.units[a]:>14.8f}{pos.fill_price(a):>12,.4f}"
                     f"{pos.basis[a]:>11,.2f}{prem['per_asset'][a]:>+10.2%}")
    lines += [
        f"{'CASH':<6}{'':>14}{'':>12}{pos.cash:>11,.2f}{'':>10}",
        "",
        f"  invested ${pos.invested:,.2f} + cash ${pos.cash:,.2f} = ${pos.capital:,.2f}",
        f"  marked now                      : ${v['value']:>10,.2f}  ({v['ret']:+.2%})",
        "",
        f"  entry cost over mark: ${prem['excess']:,.2f} ({prem['weighted']:.2%} of notional), "
        f"implying a {prem['implied_round_trip']:.2%} round trip",
        f"  against the {SPREAD_PCT:.2%} this module assumes. Minutes of drift sit "
        f"inside that number; it is a check, not a measurement.",
        "",
        f"Signals logged: {len(signals)}. No comparison is possible until a second "
        f"one arrives -- a single allocation has no performance.",
    ]
    return "\n".join(lines)


def report(start_usd: float | None = None, own_weights: dict | None = None,
           path: Path = LOG, position: Position | None = None,
           opening: Position | None = None, prices: dict | None = None) -> str:
    """Compare following the signal against the alternatives.

    `position` is the LIVE sleeve and supplies the starting capital, so every
    percentage is anchored to money actually committed rather than a round
    number chosen for convenience. `opening` is the untouched entry position --
    the control arm. Once real rebalancing begins the live sleeve IS the follow
    arm, and without the control there is nothing left to measure it against.
    """
    sigs = read_signals(path)
    if position is None and start_usd is None:
        raise ValueError("report needs either a real position or a start_usd")
    if start_usd is None:
        start_usd = position.capital

    if len(sigs) < 2:
        if position is not None and sigs:
            return opening_report(position, sigs)
        return (f"Signal log holds {len(sigs)} entry. Need at least 2 to compare "
                f"anything -- a single allocation has no performance. "
                f"Forward the next update and this starts producing numbers.")

    f = follow(sigs, start_usd)
    b = hold("BTC", sigs, start_usd, prices)
    lines = [
        f"Signals logged: {f['n']}  ({sigs[0].at[:10]} -> {sigs[-1].at[:10]})",
        f"Starting capital: ${start_usd:,.2f}"
        + ("  (as filled, from broker records)" if position is not None else "  (assumed)"),
        f"Rebalances executed: {f['trades']}   spread paid: ${f['cost']:,.2f}",
        "",
        f"  following the signal : ${f['value']:>10,.2f}  ({f['value']/start_usd-1:+.2%})",
        f"  BTC, bought and held : ${b['value']:>10,.2f}  ({b['value']/start_usd-1:+.2%})",
    ]
    if position is not None:
        a = actual(position, sigs)
        lines.append(f"  your sleeve, live    : ${a['value']:>10,.2f}  ({a['ret']:+.2%})")
    if opening is not None and position is not None and opening.units != position.units:
        px, as_of = latest_prices(sigs, prices)
        c = value_position(opening, px)
        stale = [a for a in opening.units if as_of.get(a) not in ("live", sigs[-1].at)]
        note = f"  [{', '.join(sorted(stale))} marked at {as_of[stale[0]][:10]}]" if stale else ""
        lines.append(f"  had you never traded : ${c['value']:>10,.2f}  ({c['ret']:+.2%}){note}")
    px_all, _ = latest_prices(sigs, prices)
    nw = naive_weights(sigs)
    if nw:
        nv = frozen(nw, sigs, start_usd, px_all)
        lines.append(f"  equal weight, no trade: ${nv['value']:>10,.2f}  ({nv['value']/start_usd-1:+.2%})")
    if own_weights:
        z = frozen(own_weights, sigs, start_usd, px_all)
        lines.append(f"  a paper sleeve frozen: ${z['value']:>10,.2f}  ({z['value']/start_usd-1:+.2%})")
    lines += [
        "",
        f"  spread paid as % of starting capital: {f['cost']/start_usd:.2%}",
    ]
    if position is not None and position.realized:
        lines.append(f"  realised so far: ${position.realized:+,.2f} "
                     f"(short-term; crypto has no wash-sale rule)")
    if f["n"] < 20:
        lines.append("")
        lines.append(f"n={f['n']}. This is a LOG, not a result. Do not act on it.")
    return "\n".join(lines)


# --- what the published weights themselves reveal ----------------------------
#
# These answer questions about the SIGNAL, not about its performance. That
# distinction decides what is worth running: a signal's turnover is nearly
# deterministic and converges in weeks, while its risk-adjusted return is a
# noisy estimate that needs decades. See the power note in turnover_summary.


def unit_lattice(signals: list[Signal], denom: int = 44) -> dict:
    """Test whether every published weight is an exact multiple of 1/denom.

    Adam's posts fit k/44 to within the rounding of a one-decimal percentage,
    which says the allocation is a discrete unit book rather than optimiser
    output. That matters for cost: the smallest possible move is one unit, so
    every change carries a FLOOR on the turnover it generates.

    This is a falsifiable claim, which is the point of keeping it as a check. A
    single post at, say, 20.0% breaks it, and the model should be dropped the
    day it does rather than quietly retained.
    """
    # A published percentage carries one decimal, i.e. +/-0.0005 of weight, which
    # is 0.0005*denom in units. Once that tolerance reaches half a unit, EVERY
    # conceivable weight sits within half a unit of some lattice point and the
    # test passes on anything -- it stops being evidence. Measured on the live
    # log: denom=500 reports BROKEN while denom=2000 reports HOLDS, which is the
    # signature of a vacuous test rather than a finer one.
    if 0.0005 * denom >= 0.5:
        raise ValueError(f"denom={denom} gives a tolerance of {0.0005 * denom:.2f} "
                         f"units; at >=0.5 every weight passes and the test means "
                         f"nothing. One-decimal weights can only resolve a lattice "
                         f"finer than 1/1000.")
    worst, offender = 0.0, None
    for s in signals:
        for a, w in s.weights.items():
            u = w * denom
            err = abs(u - round(u))
            if err > worst:
                worst, offender = err, (s.at[:10], a, w)
    # one decimal place on a percentage is +/-0.0005 of weight, i.e. this in units
    tol = 0.0005 * denom
    return {"denom": denom, "max_error_units": worst, "tolerance_units": tol,
            "holds": worst <= tol, "worst_case": offender,
            "unit_pct": 1.0 / denom}


def turnover(signals: list[Signal]) -> list[dict]:
    """One-way turnover between consecutive publications.

    Half the sum of absolute weight changes: the fraction of the sleeve that
    has to move. Rotating that fraction from one asset to another is two legs,
    each crossing half the spread, so the cost is turnover * SPREAD_PCT.
    """
    out = []
    for a, b in zip(signals, signals[1:]):
        keys = set(a.weights) | set(b.weights)
        t = sum(abs(b.weights.get(k, 0.0) - a.weights.get(k, 0.0)) for k in keys) / 2
        moved = {k: b.weights.get(k, 0.0) - a.weights.get(k, 0.0) for k in keys
                 if abs(b.weights.get(k, 0.0) - a.weights.get(k, 0.0)) > 1e-9}
        out.append({"from": a.at, "to": b.at, "turnover": t,
                    "cost": t * SPREAD_PCT, "moved": moved})
    return out


def turnover_summary(signals: list[Signal], posts_per_year: int = 365) -> dict:
    """Mean turnover, the annualised drag it implies, and an honest interval.

    The interval is the whole point. A point estimate off three transitions is
    not a measurement, and reporting one without its width would be the same
    error as reporting a backtest without its sample size.
    """
    ts = [r["turnover"] for r in turnover(signals)]
    n = len(ts)
    if n < 2:
        return {"n": n, "note": "need at least 2 transitions"}
    m = sum(ts) / n
    sd = (sum((t - m) ** 2 for t in ts) / (n - 1)) ** 0.5
    # Student t, two-sided 95%, by degrees of freedom.
    #
    # df=1 was MISSING from this table and fell through to the 1.96 default, so
    # the smallest possible sample -- two transitions, the state this log was
    # actually in -- got an interval 6.5x too narrow (12.706 vs 1.96). The least
    # information produced the most confident-looking answer, and the error ran
    # toward "we have measured this", which is the direction that always needs
    # checking hardest. The fallback past df=30 is now t(30) rather than the
    # normal 1.96, so an unlisted df errs wide instead of narrow.
    T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
         8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
         14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
         20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
         26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}
    t_crit = T.get(n - 1, 2.042)
    half = t_crit * sd / (n ** 0.5)
    return {"n": n, "mean": m, "sd": sd,
            "no_change_rate": sum(1 for t in ts if t == 0) / n,
            "annual_drag": m * SPREAD_PCT * posts_per_year,
            "ci": (max(m - half, 0.0), m + half),
            "annual_ci": (max(m - half, 0.0) * SPREAD_PCT * posts_per_year,
                          (m + half) * SPREAD_PCT * posts_per_year)}


def status(prices: dict | None = None, log: Path = LOG,
           pos_path: Path = POSITION, led_path: Path = LEDGER) -> str:
    """Everything the log can currently answer, in one call.

    Exists so the daily routine holds a command rather than a copy of the
    findings. Numbers quoted from a stored prompt go stale within a day; a
    command does not.
    """
    sigs = read_signals(log)
    if not sigs:
        return "No signals logged."
    out = [f"SIGNAL LOG  n={len(sigs)}  ({sigs[0].at[:10]} -> {sigs[-1].at[:10]})", ""]

    L = unit_lattice(sigs)
    out.append(f"unit lattice k/{L['denom']}: {'HOLDS' if L['holds'] else 'BROKEN'} "
               f"(worst {L['max_error_units']:.4f} units, tol {L['tolerance_units']:.4f}; "
               f"one unit = {L['unit_pct']:.3%})")
    if not L["holds"]:
        out.append(f"  -> {L['worst_case']} is off-lattice. The discrete-unit model is "
                   f"falsified; drop it rather than keeping it.")

    moved = {a for r in turnover(sigs) for a in r["moved"]}
    never = sorted(set(sigs[-1].weights) - moved)
    out.append(f"knobs that have moved: {', '.join(sorted(moved)) or 'none'}"
               + (f"   never moved: {', '.join(never)}" if never else ""))

    T = turnover_summary(sigs)
    if T.get("n", 0) >= 2:
        out += ["",
                f"turnover  mean {T['mean']:.2%}/post over n={T['n']}   "
                f"no-change {T['no_change_rate']:.0%}",
                f"          implied drag {T['annual_drag']:.1%}/yr   "
                f"95% CI [{T['annual_ci'][0]:.1%}, {T['annual_ci'][1]:.1%}]"]
        if T["n"] < 16:
            out.append(f"          n={T['n']}; ~16 posts buys +/-50% precision, ~61 buys +/-25%.")

    opening = load_position(pos_path)
    if opening is not None:
        out += ["", report(path=log, position=live_position(pos_path, led_path),
                           opening=opening, prices=prices)]
    return "\n".join(out)


if __name__ == "__main__":        # python -m hf_trading_bot.signaltrack
    print(status())
