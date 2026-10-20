"""Daily price history, and the risk statistics that need it.

WHY THIS EXISTS. Until now every number in this project came from a mark
captured at the moment a signal was forwarded -- five prices per asset, taken at
whatever hour Waldo happened to screenshot a Discord post. That is enough to
value a sleeve and nothing else. It cannot produce a volatility, a correlation,
a drawdown or a risk decomposition, because those need a regular series. So the
only thing we could say about Adam's allocation was what it returned over five
days, which is noise. This module supplies the series, and with it the questions
that are answerable NOW rather than in eight years:

    How much risk is this allocation actually running?
    Where does that risk come from -- which is not where the dollars are?
    Does his weighting beat a parameter-free one that needs no forecast?

Volatility converges fast. A 50-day sample gives a vol estimate good to about
+/-10% relative, which is a measurement. The same 50 days say essentially
nothing about expected return. Our own system is built on the first and makes no
claim on the second -- see weights_inverse_vol.

THE SOURCE IS NOT THE BROKER, AND THIS MATTERS. These are Crypto.com USD pair
closes at 00:00 UTC. Robinhood marks sit a little below them -- measured
2026-10-12: BTC -0.19%, SOL -0.25%, NEAR -0.41%, SUI -0.42% on the USDT pairs.
A level basis of 0.3% is irrelevant to a volatility or a correlation, which are
built from DIFFERENCES. It would be a real error in a valuation. So:

    use this store for RISK. Never to mark the sleeve.

Marking the sleeve is signaltrack's job and it uses broker marks, deliberately.
"""
from __future__ import annotations
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

# The project's single definition of "parse an ISO stamp into an orderable
# instant", imported rather than written again. A second copy of a rule is how
# two copies drift apart -- see the duplicate valuation guard removed from
# signaltrack on 2026-10-14. signaltrack does not import this module at module
# scope (only inside its __main__), so there is no cycle.
from .signaltrack import _ts

STORE = Path("data/prices")

# Crypto trades 24/7, so one bar's open sits essentially on the previous bar's
# close. Checking that makes a mistyped digit raise instead of quietly becoming
# a 30% one-day return that then inflates every volatility built on it -- and
# these rows WERE transcribed by hand from tool output, so that is the live risk.
#
# The tolerance is measured, not guessed. A first attempt at 2e-4 failed on real
# data, and the reason is worth keeping: the gap is not rounding, it is bid/ask
# bounce across the midnight boundary, and it scales with how thin the venue is.
# Measured over these 49 transitions:
#
#     asset   median gap   max gap     Crypto.com USD volume
#     BTC     1.3e-07      1.2e-04     ~$300M/day
#     SOL     9.0e-05      3.9e-04     ~$20M/day
#     SUI     3.3e-04      2.0e-03     ~$0.7M/day
#     NEAR    5.9e-04      3.3e-03     ~$0.9M/day
#
# BTC is continuous to the tick; NEAR gaps by 28 ticks at the median. That is a
# direct read on liquidity, and it is a caution about the SUI and NEAR series in
# particular: a thin venue's close is a noisier estimate of fair value, which
# biases a measured volatility UPWARD. 0.5% passes all of the above while still
# catching the transposed or dropped digit that would matter. It will not catch
# a typo in a trailing decimal -- nor does it need to, since one of those moves
# a volatility estimate by less than its own standard error.
CONTINUITY_TOL = 5e-3


@dataclass(frozen=True)
class Bar:
    d: date
    open: float
    high: float
    low: float
    close: float
    volume_usd: float

    def validate(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"{self.d}: open/close outside the low-high range")
        if self.low <= 0:
            raise ValueError(f"{self.d}: non-positive price")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def load(asset: str, store: Path = STORE, complete_only: bool = True) -> list[Bar]:
    """Daily bars for one asset, oldest first.

    `complete_only` drops the bar for the current UTC day, and it defaults to
    True because that bar is STILL FORMING: its "close" is just the last trade,
    and its high and low are only the range so far. Treating it as a settled
    close silently mixes a partial day into a series of full ones -- a return
    measured over six hours sitting in a column of 24-hour returns, dragging
    every volatility estimate toward whatever happened this morning. Pass False
    only to read the live level, never to compute a statistic.
    """
    path = store / f"{asset}.csv"
    if not path.exists():
        raise FileNotFoundError(f"no price history for {asset} at {path}")
    bars: list[Bar] = []
    with path.open() as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                b = Bar(d=date.fromisoformat(row["date"]),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume_usd=float(row["volume_usd"]))
                b.validate()
            except Exception as e:
                raise ValueError(f"{path}:{i} is not a usable bar -- {e}") from e
            bars.append(b)
    bars.sort(key=lambda b: b.d)
    seen = {b.d for b in bars}
    if len(seen) != len(bars):
        raise ValueError(f"{path} holds duplicate dates")
    for a, b in zip(bars, bars[1:]):
        if abs(b.open / a.close - 1) > CONTINUITY_TOL:
            raise ValueError(
                f"{path}: {a.d} closes at {a.close:,.6g} but {b.d} opens at "
                f"{b.open:,.6g}, a {b.open / a.close - 1:+.2%} jump. Crypto trades "
                f"through midnight, so these should match -- this is a gap in the "
                f"series or a typo in it, and either way the return across it is "
                f"not real.")
    if complete_only:
        bars = [b for b in bars if b.d < _today_utc()]
    return bars


def assets(store: Path = STORE) -> list[str]:
    return sorted(p.stem for p in store.glob("*.csv"))


def closes(asset: str, **kw) -> dict:
    """date -> close, for aligning several assets on their common dates."""
    return {b.d: b.close for b in load(asset, **kw)}


def align(names: list[str], **kw) -> tuple[list[date], dict]:
    """Closes for several assets restricted to the dates ALL of them have.

    Correlating two series by position rather than by date pairs Tuesday's BTC
    with Wednesday's SOL the moment one venue misses a bar, and the resulting
    number looks entirely reasonable.
    """
    series = {n: closes(n, **kw) for n in names}
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    if not common:
        raise ValueError(f"no dates common to {', '.join(names)}")
    return common, {n: [series[n][d] for d in common] for n in names}


def log_returns(px: list[float]) -> list[float]:
    """Log returns, because they add across time and so annualise honestly."""
    return [math.log(b / a) for a, b in zip(px, px[1:])]


def vol(rets: list[float], periods: int = 365) -> float:
    """Annualised volatility. 365, not 252: crypto trades every day, so a year
    really is 365 observations and using the equity convention would overstate
    it by 20%."""
    n = len(rets)
    if n < 2:
        raise ValueError("a volatility needs at least 2 returns")
    m = sum(rets) / n
    var = sum((r - m) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var * periods)


def vol_se(v: float, n: int) -> float:
    """Standard error of a volatility estimate: roughly v/sqrt(2n).

    Reported alongside every vol because the point of this module is that vol
    is measurable at n=50 while return is not, and that claim is empty without
    the width that justifies it.
    """
    return v / math.sqrt(2 * n)


def corr(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"series are {len(a)} and {len(b)} long -- not aligned")
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    ca = [x - ma for x in a]
    cb = [x - mb for x in b]
    da, db = math.sqrt(sum(x * x for x in ca)), math.sqrt(sum(x * x for x in cb))
    if da == 0 or db == 0:
        raise ValueError("a series with no variance has no correlation")
    return sum(x * y for x, y in zip(ca, cb)) / (da * db)


def corr_se(rho: float, n: int) -> float:
    """Standard error of a correlation, (1-rho^2)/sqrt(n). At n=49 and rho=0.5
    that is 0.107 -- so "0.52" and "0.73" are not distinguishable here."""
    return (1 - rho ** 2) / math.sqrt(n)


def covariance(names: list[str], periods: int = 365, **kw) -> tuple[dict, dict, int]:
    """Annualised covariance matrix, the vols on its diagonal, and the sample
    size every entry rests on."""
    _, px = align(names, **kw)
    rets = {n: log_returns(px[n]) for n in names}
    k = len(next(iter(rets.values())))
    vols = {n: vol(rets[n], periods) for n in names}
    cov = {}
    for i in names:
        for j in names:
            r = 1.0 if i == j else corr(rets[i], rets[j])
            cov[(i, j)] = r * vols[i] * vols[j]
    return cov, vols, k


def portfolio_risk(weights: dict, periods: int = 365, **kw) -> dict:
    """What risk an allocation is actually running, and where it comes from.

    `weights` are fractions of the WHOLE sleeve, cash included, exactly as Adam
    publishes them. Cash is carried at zero volatility, which is why his 31.8%
    slice is the single largest risk decision in the book and never appears as
    one.

    THE NUMBER WORTH HAVING is `risk_share`. A dollar weight says how much of
    the sleeve an asset occupies; a risk share says how much of the sleeve's
    movement it causes, and the two are not close when the assets differ in
    volatility by 3x. Marginal contribution is w_i*(Sigma w)_i/sigma_p, which
    sums exactly to sigma_p -- so these shares sum to 1 and can be read as a
    decomposition rather than an analogy.
    """
    risky = {a: w for a, w in weights.items() if a != "CASH" and w != 0.0}
    if not risky:
        raise ValueError("no risky weight to measure")
    # Name what is missing AND how much of the book it is, before computing
    # anything. Decomposing only the assets that happen to have history would
    # hand back shares that sum to 1.0 across a subset of the sleeve -- a number
    # that looks like an answer and is not one. ZRO arrived 2026-10-15 with no
    # usable series anywhere reachable: Crypto.com's ZRO_USD does ~$13k/day and
    # breaks continuity on 17 of 30 transitions, ZRO_USDT does ~$1.4k/day, and
    # Robinhood serves no crypto history at all.
    store = kw.get("store", STORE)
    absent = sorted(a for a in risky if not (store / f"{a}.csv").exists())
    if absent:
        share = sum(risky[a] for a in absent)
        raise ValueError(
            f"no price history for {', '.join(absent)}, which is "
            f"{share:.1%} of the sleeve ({share / sum(risky.values()):.1%} of the "
            f"invested part). Refusing to decompose the rest: shares computed over "
            f"the assets that happen to have data would sum to 1.0 across a subset "
            f"of the book and read as a full answer.")
    names = sorted(risky)
    cov, vols, k = covariance(names, periods, **kw)
    var = sum(risky[i] * risky[j] * cov[(i, j)] for i in names for j in names)
    sigma = math.sqrt(var)
    # (Sigma w)_i, the marginal variance contribution of a unit of asset i
    mc = {i: sum(cov[(i, j)] * risky[j] for j in names) for i in names}
    rc = {i: risky[i] * mc[i] / sigma for i in names}
    gross = sum(risky.values())
    weighted_vol = sum(risky[i] * vols[i] for i in names)
    return {
        "sigma": sigma,                        # annualised sleeve vol
        "sigma_se": vol_se(sigma, k),
        "n_returns": k,
        "vols": vols,
        "invested": gross,
        "cash": weights.get("CASH", 0.0),
        "risk_contrib": rc,                    # sums to sigma
        "risk_share": {i: rc[i] / sigma for i in names},
        "weight_share": {i: risky[i] / gross for i in names},
        # 1.0 means correlation gave nothing back; higher means the legs offset
        "diversification": weighted_vol / sigma,
        "daily_sigma": sigma / math.sqrt(periods),
    }


def weights_inverse_vol(universe: list[str], cash: float,
                        periods: int = 365, **kw) -> dict:
    """OUR OWN allocation, and the point is what it does NOT contain.

    Each asset is weighted inversely to its own volatility, scaled to whatever
    cash level it is given. That is the whole rule. It has ZERO fitted
    parameters: nothing is optimised, no history of Adam is used, no return is
    forecast, and there is no threshold, lookback choice or ranking that could
    have been tuned to make the backtest look better. With five published
    signals in hand, anything fitted would be curve-drawing -- the error record
    in knowledge/process/error-modes.md is largely about exactly that mistake.

    It is a real competitor rather than a straw man. Equal weight ignores that
    NEAR has run at roughly three times BTC's volatility, so an equal-weight
    book is a NEAR book wearing a diversified label. Inverse-vol fixes only
    that, and if Adam's hand-set weights cannot beat it, his weighting is not
    what is adding value -- his asset SELECTION and his cash level might still be.

    Deliberately left alone: the cash weight. That is the largest risk lever in
    the book and setting it requires a view, which this rule refuses to have.
    Pass Adam's own so the comparison isolates weighting.
    """
    if not 0.0 <= cash < 1.0:
        raise ValueError(f"cash weight {cash} is not in [0, 1)")
    _, vols, _ = covariance(sorted(universe), periods, **kw)
    inv = {a: 1.0 / vols[a] for a in vols}
    tot = sum(inv.values())
    return {**{a: (1.0 - cash) * v / tot for a, v in inv.items()}, "CASH": cash}


def drawdown(px: list[float]) -> dict:
    """Worst peak-to-trough fall in the series, and where it happened."""
    peak = px[0]
    worst, at = 0.0, 0
    for i, p in enumerate(px):
        peak = max(peak, p)
        dd = p / peak - 1
        if dd < worst:
            worst, at = dd, i
    return {"max": worst, "at": at}


# --- the store that needs no third party ------------------------------------
#
# Everything above comes from Crypto.com. That is fine for BTC, SOL, SUI and
# NEAR and useless for anything smaller: ZRO arrived on 2026-10-15 and none of
# Crypto.com's three ZRO venues carries real flow -- ZRO_USD ~$13k/day, ZRO_USDT
# ~$1.4k/day, ZROUSD-PERP ~$4k/day with one session at $33. All three break the
# continuity check. The obvious alternatives are all refused by this
# environment's network policy: api.coingecko.com, min-api.cryptocompare.com,
# api.binance.com and api.kraken.com each answer 403 to CONNECT at the gateway.
#
# So this records OUR OWN series, from the venue we actually trade on. It needs
# no outside host, it cannot be revoked by a policy, and its prices are the ones
# Waldo's orders really fill against rather than a third party's approximation.
# The cost is that it has no past: it starts the day it starts, and a volatility
# wants roughly 30 observations. That is the trade, stated plainly.

MARKS = Path("data/prices/marks.jsonl")


def record_marks(marks: dict, at: str | None = None, source: str = "robinhood",
                 path: Path = MARKS) -> int:
    """Append one dated snapshot of broker marks. One row per asset.

    `at` is when the quote was READ, and defaults to now. It is emphatically not
    the signal's publication time: passing that stamped marks observed at 00:13
    with a 20:12 timestamp, putting prices four hours in their own past and
    smuggling an exact duplicate of the next day's snapshot past the one-per-day
    guard under a different date. A price row's stamp is a claim about when the
    price existed, and it has to be true.

    Refuses to write twice for the same asset on the same calendar day: a
    double capture would put two observations of one day into a series of daily
    ones, which quietly halves that day's apparent return and biases the
    volatility built on it. Same failure as the duplicate signal, same refusal.
    """
    at = at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    day = _ts(at).date().isoformat()
    existing = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                existing.add((r["asset"], r["at"][:10]))
    dupes = sorted(a for a in marks if (a, day) in existing)
    if dupes:
        raise ValueError(f"{', '.join(dupes)} already has a mark for {day} -- "
                         f"two observations of one day is not a daily series")
    for a, px in marks.items():
        if px <= 0:
            raise ValueError(f"{a} marked at {px}; a price must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for a in sorted(marks):
            f.write(json.dumps({"at": at, "asset": a, "mark": marks[a],
                                "source": source}) + "\n")
    return len(marks)


def read_marks(path: Path = MARKS) -> dict:
    """asset -> [(date, mark)], oldest first."""
    if not path.exists():
        return {}
    out: dict[str, list] = {}
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            d = _ts(r["at"]).date()
        except Exception as e:
            raise ValueError(f"{path}:{i} is not a usable mark -- {e}") from e
        out.setdefault(r["asset"], []).append((d, float(r["mark"])))
    return {a: sorted(v) for a, v in out.items()}


def marks_missing_today(assets: list[str], path: Path = MARKS) -> list[str]:
    """Which of `assets` have no mark recorded for the current UTC day.

    Exists because the capture does NOT run by itself, contrary to what the
    commit that added it claimed. record_marks fires inside the brief only when
    live quotes are passed, and the scheduled routines invoke the module with no
    quotes at all -- they have no broker access from the command line. Six
    routine runs between 2026-10-14 and 2026-10-19 recorded nothing, and the
    series that did accumulate came entirely from hand-typed --prices calls. It
    shows in the data: BTC got four captures because it was quoted most often,
    while NEAR and SOL -- the two legs the frozen control arm still holds and
    most needs priced -- got two each.

    A series that depends on someone remembering is the same failure as a
    routine holding stale facts instead of a command. It cannot be fixed by
    making the CLI fetch prices it has no credentials for, so instead the
    ABSENCE is made loud: every brief says plainly when today's marks are
    missing, and who is missing them.
    """
    today = _today_utc()
    have = {a for a, v in read_marks(path).items() if v and v[-1][0] >= today}
    return sorted(set(assets) - have)


def marks_status(path: Path = MARKS, need: int = 30) -> dict:
    """How far this store is from being able to carry a volatility.

    Exists so the gap is a countdown with a date on it rather than a standing
    apology. `need` is 30 because a volatility from n returns carries a standard
    error near vol/sqrt(2n): 30 gives about +/-13% relative, which is roughly
    what the Crypto.com series already provide at 49 bars.
    """
    m = read_marks(path)
    return {a: {"n": len(v), "first": v[0][0].isoformat(),
                "last": v[-1][0].isoformat(), "short_by": max(need - len(v), 0)}
            for a, v in sorted(m.items())}
