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
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

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
