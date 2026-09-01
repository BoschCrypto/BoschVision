"""Cross-sectional portfolio backtester for the Gated Momentum strategy.

The existing `backtest.py` replays one symbol with one position at a time. That
shape cannot express this strategy at all: Gated Momentum ranks a whole universe
against itself, holds ~20 names simultaneously with unequal weights, and
rebalances on a calendar rather than on a signal. Hence a separate engine.

Design commitments, each of which exists to stop a specific way this kind of
backtest lies:

* **A one-session gap between signal and fill.** Signals are computed from data
  through a month-end close; the trade fills at the *next* session's close. No
  strategy here gets to trade on a price it used to make the decision.
* **Costs on turnover, always.** Charged as a fraction of the weight actually
  traded at every rebalance, including the initial build and the liquidation
  into a risk-off regime.
* **Survivorship bias is declared, not assumed away.** The universe arrives with
  a `UniverseMode` and the report refuses to stay quiet about which one it was.
* **Ablations are first-class.** A component that cannot be shown to earn its
  place gets deleted, so the engine makes it cheap to run the strategy without
  each piece.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Sequence

from hf_trading_bot import factors
from hf_trading_bot.data.bars import Bar


class UniverseMode(str, Enum):
    """How the candidate universe was assembled — determines how much the
    result can be trusted."""

    POINT_IN_TIME = "point_in_time"
    """Index membership as it stood on each rebalance date, delisted names
    included. The only mode whose numbers mean what they appear to mean."""

    CURRENT_MEMBERS = "current_members"
    """Today's index members, applied backwards. SURVIVORSHIP-BIASED: every
    company that failed out of the index between the start date and now is
    missing, and those are disproportionately the momentum losers. Inflates
    results in the flattering direction. Usable for plumbing checks; not for
    deciding whether to commit capital."""

    STATIC_LIST = "static_list"
    """A hand-supplied list. Bias depends entirely on how it was chosen — if it
    was picked by looking at what did well, the backtest is circular."""


@dataclass(frozen=True)
class GatedMomentumParams:
    """Strategy parameters. Defaults match the specification.

    `quality_fn` is the deliberate gap. Gross profitability needs *point-in-time*
    fundamentals — what was reported and known on the rebalance date, not what
    the company's financials look like today. Applying current fundamentals to
    historical dates is look-ahead bias of the worst kind, because it is
    invisible in the output. Left as None, the quality gate is skipped and the
    report says so rather than quietly running a different strategy than the one
    specified.
    """

    n_positions: int = 20
    rank_buffer: int = 5
    min_dollar_volume: float = 50_000_000.0
    min_price: float = 10.0
    pct_52w_high_floor: float = 0.85
    max_weight: float = 0.15
    regime_sma_months: int = 10
    regime_trend_months: int = 24
    round_trip_bps: float = 5.0

    use_regime_gate: bool = True
    use_52w_filter: bool = True
    use_inverse_vol: bool = True
    quality_fn: Optional[Callable[[str, str], Optional[float]]] = None
    quality_percentile: float = 0.50

    @property
    def one_way_cost(self) -> float:
        return (self.round_trip_bps / 2.0) / 10_000.0


@dataclass
class RebalanceRecord:
    date: str
    regime_on: bool
    n_candidates: int
    holdings: dict[str, float]
    turnover: float
    cost_paid: float
    equity_before: float


@dataclass
class BacktestResult:
    params: GatedMomentumParams
    universe_mode: UniverseMode
    label: str
    start: str
    end: str
    dates: list[str]
    equity: list[float]
    daily_returns: list[float]
    rebalances: list[RebalanceRecord] = field(default_factory=list)
    skipped_symbols: dict[str, str] = field(default_factory=dict)

    # ---- summary statistics -------------------------------------------------

    @property
    def years(self) -> float:
        return len(self.dates) / factors.TRADING_DAYS_YEAR if self.dates else 0.0

    @property
    def total_return(self) -> Optional[float]:
        if len(self.equity) < 2 or self.equity[0] <= 0:
            return None
        return self.equity[-1] / self.equity[0] - 1

    @property
    def cagr(self) -> Optional[float]:
        tr = self.total_return
        if tr is None or self.years <= 0:
            return None
        growth = 1 + tr
        if growth <= 0:
            return -1.0
        return growth ** (1 / self.years) - 1

    @property
    def sharpe(self) -> Optional[float]:
        return factors.annualised_sharpe(self.daily_returns)

    @property
    def max_drawdown(self) -> Optional[float]:
        return factors.max_drawdown(self.equity)

    @property
    def total_cost_paid(self) -> float:
        return sum(r.cost_paid for r in self.rebalances)

    @property
    def annual_turnover(self) -> Optional[float]:
        """One-way turnover per year. 1.0 means the book is replaced once."""
        if self.years <= 0 or not self.rebalances:
            return None
        return sum(r.turnover for r in self.rebalances) / 2.0 / self.years

    @property
    def annual_cost_drag(self) -> Optional[float]:
        """Realised trading cost as an annualised fraction of equity.

        This is the number kill-criterion K2 watches: the whole case for running
        a high-turnover factor strategy in a small account rests on cost staying
        far below the edge.
        """
        if self.years <= 0 or not self.rebalances:
            return None
        weighted = sum(
            r.cost_paid / r.equity_before for r in self.rebalances if r.equity_before > 0
        )
        return weighted / self.years

    @property
    def pct_time_deployed(self) -> Optional[float]:
        if not self.rebalances:
            return None
        return sum(1 for r in self.rebalances if r.holdings) / len(self.rebalances)


def _align_calendar(
    price_data: dict[str, list[Bar]], calendar_symbol: str
) -> tuple[list[str], dict[str, dict[str, Bar]]]:
    """Build the master trading calendar and a date-indexed view of each symbol.

    The calendar comes from one reference symbol (SPY) rather than the union of
    all symbols' dates. A union would invent trading days out of one venue's bad
    tick, and it would let a symbol's own data gaps shift its momentum window
    relative to everyone else's.
    """
    if calendar_symbol not in price_data:
        raise ValueError(f"calendar symbol {calendar_symbol!r} missing from price data")
    dates = [b.t for b in price_data[calendar_symbol]]
    indexed = {sym: {b.t: b for b in bars} for sym, bars in price_data.items()}
    return dates, indexed


def _series_on_calendar(bars: list[Bar], dates: Sequence[str]) -> Optional[list[Bar]]:
    """Project a symbol's bars onto the master calendar, forward-filling gaps.

    Returns None if the symbol has no data at all. A forward-filled bar repeats
    the previous close, so a halted or thinly-traded name contributes zero return
    on missing days rather than a fabricated jump.
    """
    by_date = {b.t: b for b in bars}
    out: list[Bar] = []
    last: Optional[Bar] = None
    for d in dates:
        b = by_date.get(d)
        if b is not None:
            last = b
            out.append(b)
        elif last is not None:
            out.append(Bar(t=d, o=last.c, h=last.c, l=last.c, c=last.c, v=0.0))
        else:
            out.append(Bar(t=d, o=0.0, h=0.0, l=0.0, c=0.0, v=0.0))
    return out if last is not None else None


def _select(
    symbols: Sequence[str],
    series: dict[str, list[Bar]],
    signal_idx: int,
    params: GatedMomentumParams,
    previous: set[str],
) -> tuple[list[str], dict[str, float], int]:
    """Rank the universe and pick the book. Returns (chosen, vols, n_candidates).

    Everything is computed at `signal_idx`, which is strictly earlier than the
    fill bar the caller uses.
    """
    scored: list[tuple[float, str, float]] = []
    for sym in symbols:
        bars = series.get(sym)
        if bars is None or bars[signal_idx].c <= 0:
            continue
        if bars[signal_idx].c < params.min_price:
            continue
        adv = factors.avg_dollar_volume(bars, signal_idx)
        if adv is None or adv < params.min_dollar_volume:
            continue
        mom = factors.momentum_12_1(bars, signal_idx)
        if mom is None:
            continue
        if params.use_52w_filter:
            prox = factors.pct_of_52w_high(bars, signal_idx)
            if prox is None or prox < params.pct_52w_high_floor:
                continue
        if params.quality_fn is not None:
            q = params.quality_fn(sym, bars[signal_idx].t)
            if q is None:
                continue
            scored.append((mom, sym, q))
            continue
        scored.append((mom, sym, 0.0))

    n_candidates = len(scored)
    if not scored:
        return [], {}, 0

    # Quality gate runs before the momentum cut, not blended into it: no amount
    # of momentum should be able to buy a junk name into the book.
    if params.quality_fn is not None:
        scored.sort(key=lambda x: x[2], reverse=True)
        keep = max(1, int(len(scored) * params.quality_percentile))
        scored = scored[:keep]

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [s for _, s, _ in scored]

    # Rank buffer: an incumbent is kept while it stays inside the extended band,
    # so a name slipping from 20th to 21st does not trigger a round trip with no
    # informational content.
    band = set(ranked[: params.n_positions + params.rank_buffer])
    chosen = [s for s in ranked[: params.n_positions] if s in band]
    held_still_ok = [s for s in ranked if s in previous and s in band and s not in chosen]
    for s in held_still_ok:
        if len(chosen) >= params.n_positions:
            break
        chosen.append(s)
    for s in ranked:
        if len(chosen) >= params.n_positions:
            break
        if s not in chosen:
            chosen.append(s)
    chosen = chosen[: params.n_positions]

    vols: dict[str, float] = {}
    for s in chosen:
        v = factors.atr_pct(series[s], signal_idx) if params.use_inverse_vol else 1.0
        vols[s] = v if v and v > 0 else 0.02
    return chosen, vols, n_candidates


def run(
    price_data: dict[str, list[Bar]],
    universe: Sequence[str],
    universe_mode: UniverseMode,
    params: Optional[GatedMomentumParams] = None,
    benchmark_symbol: str = "SPY",
    label: str = "gated_momentum",
    initial_equity: float = 100_000.0,
) -> BacktestResult:
    """Replay the strategy across the price data.

    `price_data` must contain `benchmark_symbol` (used both as the trading
    calendar and as the regime input) plus every symbol in `universe`.
    """
    params = params or GatedMomentumParams()
    dates, _ = _align_calendar(price_data, benchmark_symbol)
    if len(dates) < factors.TRADING_DAYS_YEAR + 2:
        raise ValueError(
            f"need > {factors.TRADING_DAYS_YEAR} sessions to compute a 12-month "
            f"signal; got {len(dates)}"
        )

    series: dict[str, list[Bar]] = {}
    skipped: dict[str, str] = {}
    for sym in universe:
        bars = price_data.get(sym)
        if not bars:
            skipped[sym] = "no data"
            continue
        projected = _series_on_calendar(bars, dates)
        if projected is None:
            skipped[sym] = "no overlapping dates"
            continue
        series[sym] = projected
    spy = _series_on_calendar(price_data[benchmark_symbol], dates)
    assert spy is not None

    # Signal dates are month ends; fills happen on the following session.
    signal_days = [
        i for i in factors.month_end_indices(spy) if i >= factors.TRADING_DAYS_YEAR and i + 1 < len(dates)
    ]
    fill_days = {i + 1 for i in signal_days}

    equity = initial_equity
    holdings: dict[str, float] = {}   # symbol -> shares
    equity_curve: list[float] = []
    daily_returns: list[float] = []
    records: list[RebalanceRecord] = []
    prev_value = equity

    first_fill = min(fill_days) if fill_days else len(dates)

    for i, date in enumerate(dates):
        # Mark the book to today's closes before deciding anything.
        if holdings:
            equity = sum(qty * series[s][i].c for s, qty in holdings.items())

        if i in fill_days:
            signal_idx = i - 1
            regime = True
            if params.use_regime_gate:
                gate = factors.regime_risk_on(
                    spy, signal_idx, params.regime_sma_months, params.regime_trend_months
                )
                # Insufficient history means "cannot evaluate the gate", which
                # must not be read as permission to deploy.
                regime = bool(gate)

            if regime:
                chosen, vols, n_cand = _select(
                    list(series.keys()), series, signal_idx, params, set(holdings)
                )
                target = (
                    factors.inverse_vol_weights(vols, params.max_weight)
                    if params.use_inverse_vol
                    else {s: 1.0 / len(chosen) for s in chosen} if chosen else {}
                )
            else:
                chosen, target, n_cand = [], {}, 0

            current_w = (
                {s: qty * series[s][i].c / equity for s, qty in holdings.items()}
                if equity > 0
                else {}
            )
            names = set(current_w) | set(target)
            turnover = sum(abs(target.get(s, 0.0) - current_w.get(s, 0.0)) for s in names)
            cost = turnover * params.one_way_cost * equity

            records.append(
                RebalanceRecord(
                    date=date,
                    regime_on=regime,
                    n_candidates=n_cand,
                    holdings=dict(target),
                    turnover=turnover,
                    cost_paid=cost,
                    equity_before=equity,
                )
            )

            equity -= cost
            holdings = {
                s: (w * equity) / series[s][i].c
                for s, w in target.items()
                if series[s][i].c > 0
            }

        if i >= first_fill:
            equity_curve.append(equity)
            if prev_value > 0 and len(equity_curve) > 1:
                daily_returns.append(equity / prev_value - 1)
            prev_value = equity

    used_dates = dates[first_fill:] if first_fill < len(dates) else []
    return BacktestResult(
        params=params,
        universe_mode=universe_mode,
        label=label,
        start=used_dates[0] if used_dates else "",
        end=used_dates[-1] if used_dates else "",
        dates=used_dates,
        equity=equity_curve,
        daily_returns=daily_returns,
        rebalances=records,
        skipped_symbols=skipped,
    )


def buy_and_hold(
    price_data: dict[str, list[Bar]], symbol: str, start_date: str, initial_equity: float = 100_000.0
) -> Optional[BacktestResult]:
    """Benchmark: hold `symbol` from `start_date` to the end of the data.

    Aligned to the strategy's own start so the comparison covers one window.
    """
    bars = [b for b in price_data.get(symbol, []) if b.t >= start_date]
    if len(bars) < 2 or bars[0].c <= 0:
        return None
    qty = initial_equity / bars[0].c
    equity = [qty * b.c for b in bars]
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    return BacktestResult(
        params=GatedMomentumParams(),
        universe_mode=UniverseMode.STATIC_LIST,
        label=f"buy_and_hold_{symbol}",
        start=bars[0].t,
        end=bars[-1].t,
        dates=[b.t for b in bars],
        equity=equity,
        daily_returns=rets,
    )


# --- Ablations -------------------------------------------------------------
#
# Each entry removes exactly one component. A component that does not improve
# risk-adjusted return relative to the full strategy has not earned its place
# and should be deleted from the spec rather than defended.

ABLATIONS: dict[str, dict] = {
    "full": {},
    "no_regime_gate": {"use_regime_gate": False},
    "no_52w_filter": {"use_52w_filter": False},
    "equal_dollar_weight": {"use_inverse_vol": False},
    "no_cost_model": {"round_trip_bps": 0.0},
}


def run_ablations(
    price_data: dict[str, list[Bar]],
    universe: Sequence[str],
    universe_mode: UniverseMode,
    base: Optional[GatedMomentumParams] = None,
    benchmark_symbol: str = "SPY",
    only: Optional[Sequence[str]] = None,
) -> dict[str, BacktestResult]:
    base = base or GatedMomentumParams()
    keys = list(only) if only else list(ABLATIONS)
    results: dict[str, BacktestResult] = {}
    for name in keys:
        overrides = ABLATIONS.get(name)
        if overrides is None:
            continue
        params = GatedMomentumParams(**{**base.__dict__, **overrides})
        results[name] = run(
            price_data, universe, universe_mode, params,
            benchmark_symbol=benchmark_symbol, label=name,
        )
    return results


# --- Honesty checks --------------------------------------------------------

MIN_REBALANCES_MEANINGFUL = 60
"""Five years of monthly decisions. Below this a backtest cannot separate a
strategy from the particular decade it happened to run in."""


def integrity_warnings(result: BacktestResult) -> list[str]:
    """Everything about this run that should stop a reader trusting the number.

    Returned as a list so the caller cannot print the performance table without
    also having these in hand.
    """
    warnings: list[str] = []

    if result.universe_mode is UniverseMode.CURRENT_MEMBERS:
        warnings.append(
            "SURVIVORSHIP BIAS: universe is today's index members applied "
            "backwards. Companies that failed out of the index are absent, and "
            "they are disproportionately the momentum losers this strategy is "
            "supposed to avoid. Results are inflated by an unknown but "
            "systematically favourable amount. Not a basis for committing capital."
        )
    elif result.universe_mode is UniverseMode.STATIC_LIST:
        warnings.append(
            "UNVERIFIED UNIVERSE: a hand-supplied symbol list. If the list was "
            "chosen with any knowledge of what performed well, the backtest is "
            "circular."
        )

    if result.params.quality_fn is None:
        warnings.append(
            "QUALITY GATE NOT TESTED: no point-in-time fundamentals were "
            "supplied, so the gross-profitability filter was skipped. This run "
            "tests price-based Gated Momentum, not the full specification — the "
            "quality gate's contribution is unmeasured, and its ablation cannot "
            "be run."
        )

    n = len(result.rebalances)
    if n == 0:
        warnings.append("NO REBALANCES: nothing was evaluated.")
    elif n < MIN_REBALANCES_MEANINGFUL:
        warnings.append(
            f"SHORT SAMPLE: {n} rebalance decisions (< {MIN_REBALANCES_MEANINGFUL}). "
            f"Too few to distinguish the strategy from the regime it ran in."
        )

    if result.dates:
        span_years = result.years
        if span_years < 10:
            warnings.append(
                f"NARROW WINDOW: {span_years:.1f} years. The specification calls "
                f"for 2005-2026 so the strategy meets 2008, 2020 and 2022 — three "
                f"structurally different drawdowns. A momentum strategy that has "
                f"not met 2008-09 has not been tested where it is known to fail."
            )

    if result.params.round_trip_bps == 0:
        warnings.append("COSTS DISABLED: frictionless run, for ablation only.")

    deployed = result.pct_time_deployed
    if deployed is not None and deployed < 0.5:
        warnings.append(
            f"MOSTLY IN CASH: deployed at only {deployed:.0%} of rebalances. "
            f"Comparing raw return against buy-and-hold without accounting for "
            f"that is misleading in the strategy's favour."
        )

    return warnings
