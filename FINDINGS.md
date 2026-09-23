# Findings

Dated conclusions from testing, kept here so they don't quietly get
re-litigated (and re-paid for) a year later. Each entry is append-only —
if a later test contradicts one, add a new entry, don't edit the old one.

## 2026-08-14 — Five systematic strategies do not beat buy-and-hold

**Test:** `hf-bot sweep`, 24 symbols, 5 strategies, 2021-01-01 → 2026-08-14.

| strategy | beat buy&hold | hit rate | median excess | trades |
|---|---|---|---|---|
| momentum_90d | 5/24 | 21% | −71.2 pts | 680 |
| rsi_mean_reversion | 8/24 | 33% | −37.4 pts | 185 |
| sma_crossover | 6/24 | 25% | −51.0 pts | 360 |
| macd_crossover | 7/24 | 29% | −35.6 pts | 1,294 |
| bollinger_breakout | 9/24 | 38% | −57.9 pts | 591 |

A coin flip is 50%. Every strategy landed below that, with large negative
median excess, and the result held across five mechanically different
signals — that consistency is what makes it a finding rather than one bad
draw.

**Audit.** Reviewed `backtest.py` against seven specific failure modes
before trusting this:

1. **Open-trade exclusion — real bug, fixed.** `stats()` was dropping any
   position still open at the window's end from `total_return_pct`
   entirely, while buy-and-hold — which never has an "open position"
   problem — always captures the final price. This specifically penalized
   trend-followers in this rising window. Fixed: open positions are now
   marked to the final close for the equity curve (kept out of realized
   win/loss stats, since they haven't resolved). Did not flip the
   conclusion — it can only ever mis-price one trade per symbol per
   strategy, nowhere near enough to close a −71pt median gap.
2. **Look-ahead bias — favors the strategies.** Signal and entry both use
   the same bar's close. Real execution would be worse, not better, so
   the true gap is probably understated here, not overstated.
3. **Cash drag — penalizes the strategies, small.** Idle cash between
   trades earns 0% in the sim vs. buy-and-hold's constant full exposure.
   Crediting T-bill yields for idle periods adds at most a few points over
   5.5 years — not enough to matter against these gaps.
4. **Compounding model — consistent.** Same sequential full-reinvestment
   convention as `buy_and_hold()`; the two numbers are comparable.
5. **Regime dependence — real caveat.** 2021–2026 is a strong bull window,
   which is exactly where anything that steps out to cash struggles
   against buy-and-hold. Limits how far this generalizes to other
   regimes; doesn't invalidate what was tested.
6. **Transaction costs — unmodeled, immaterial at this scale.** Even
   macd_crossover's 1,294 trades add roughly 1pt of cumulative drag per
   symbol at plausible slippage — far short of a −35.6pt median.
7. **No other bugs.** Benchmark and strategy windows use the identical
   fetched bar list; `years` is computed once and shared.

**Verdict: robust.** Every bias found either favors the strategies or is
too small to matter. This does not mean no systematic strategy could ever
work — it means these five specific rules, over this specific window, on
this specific universe, did not earn their keep. Re-testing the same five
rules again without a new hypothesis is data-mining, not validation.

**Decision:** pivot the account to an index core, keep these strategies as
the measured baseline (never delete — they're what any future rule has to
beat), and route active decisions through the research committee +
decision journal instead. See `hf-bot portfolio compare` for the ongoing
scorecard against SPY.

---

## Gated Momentum, first real backtest — 23 September 2026

Ran at last on real bars: 141 of 142 universe symbols plus SPY (MMC
unavailable), daily split-adjusted, 2011-02-01 → 2026-09-21, 15.6 years,
188 monthly rebalances. Data came from Robinhood historicals via
`ximport`; the yfinance path this was blocked on for three weeks was never
the only option.

**The specification as written loses to doing nothing.**

| | return | CAGR | maxDD | Sharpe |
|---|---|---|---|---|
| gated_momentum (full spec) | 352.0% | 10.2% | −31.3% | 0.66 |
| buy & hold SPY | 491.8% | 12.1% | −34.1% | 0.75 |
| **momentum_only** (both filters removed) | **1043.0%** | **16.9%** | −31.9% | **0.88** |

### What is robust: both filters destroy value, in both halves

| | 2012-02 → 2018-12 | 2020-02 → 2026-09 |
|---|---|---|
| full spec | 111.1% · Sharpe 0.84 | 58.4% · Sharpe 0.53 |
| no_regime_gate | 165.8% · 0.98 | 127.5% · 0.67 |
| no_52w_filter | 122.3% · 0.89 | 92.7% · 0.68 |

The regime gate — the component the strategy is named for — loses to its
own removal in two independent ~7-year periods, on both return and Sharpe.
This is the most trustworthy result in the run, because both arms of an
ablation read the identical (biased) universe, so the comparison is
internally fair even though the absolute numbers are not.

The gate does cut drawdown where it is measured per-period (−15.5% vs
−21.8%; −18.8% vs −31.3%) — it simply costs far more return than that
protection is worth. Over the full period the two show the same −31.3%,
because the worst drawdown happened while the gate was deployed. Reading
only the full-period line would have hidden a real effect.

The 4 September isolated SPY test said the gate "halves drawdown, costs
3pts CAGR". As an overlay on a momentum book that trade-off does not
survive: you pay the CAGR and keep most of the drawdown.

**One component survived.** Inverse-volatility weighting beat equal-dollar
weight on Sharpe in both sub-periods (0.84 vs 0.79; 0.53 vs 0.52) despite
losing on raw return. It stays.

### What is NOT robust: that momentum_only beats SPY

The 1043% figure is not credible and must not be quoted as an edge. The
universe is a hand-supplied list of 142 companies that all still trade
today, and I chose it. Cross-sectional momentum on a survivor list is the
textbook case where survivorship bias does maximum damage: the strategy
ranks and buys winners from a set selected *because* they won. Buy-and-hold
SPY gets no such help, so the comparison is structurally rigged in
momentum's favour.

Both arms of an ablation share the bias and cancel most of it. A strategy
measured against SPY does not. So:

- "The gate and the 52-week filter hurt" — **believe this.**
- "Momentum-only earns 16.9% CAGR" — **do not believe this.**

### Consequences

1. Delete the regime gate and the 52-week filter from the specification.
   The harness's own rule: a component earns its place only if the full
   strategy beats the ablation that removes it. Neither does, twice.
2. Stop reporting the gate in the daily monitor. It has been reported every
   weekday since 1 September as though it meant something.
3. The quality gate remains **untested** — no point-in-time fundamentals
   were supplied, so gross profitability was skipped entirely. This run
   measured price-based momentum, not the full spec.
4. A point-in-time universe is now the blocking task, not an improvement.
   Until it exists, no live allocation is justified by this run.

**Decision: nothing is funded off this backtest.** It cost a backtest
instead of the account, which is the outcome a backtest is for.

### The survivorship bias cannot be removed with this data source — tested

A point-in-time universe needs bars for companies that no longer trade.
Robinhood does not serve them. Tested directly:

    get_equity_historicals(["TWTR","SIVB","FRC","ATVI","CERN"])
    -> API error 400
       inactive_instruments: ["TWTR","ATVI","CERN"]   (acquired)
       missing_instruments:  ["SIVB","FRC"]           (failed banks)

Both failure modes matter and they are the two that bias momentum most:
acquisitions (often at a premium, after strength) and outright failures
(terminal weakness). A universe that can only contain survivors is missing
precisely the tails that decide whether the strategy works.

So the blocking task stated above is **not achievable with the available
data**, and no amount of engineering changes that. Removing this bias
requires a point-in-time source that retains delisted securities — CRSP,
Norgate, Sharadar or equivalent, all paid.

**How large is the doubt?** momentum_only measured 16.9% CAGR against SPY's
12.1% — a 4.8pp edge. Published estimates of survivorship bias in US
large-cap backtests run roughly 1-4pp/yr depending on period and
construction. The measured edge is the same order of magnitude as the bias
that would manufacture it from nothing. **The two cannot be separated with
this data.** That is not a reason to assume the edge is zero; it is a reason
to refuse to act as though it is positive.

**Stopping here deliberately.** The tempting next move is to try variants
until one survives — sector-ETF rotation, different lookbacks, different
holding counts. Each is cheap to run and each would produce a number. That
is data-mining, and this file already records the rule against it: re-testing
without a new hypothesis is not validation. The honest state of the project
is:

- Two components measured and deleted. That knowledge is permanent.
- One component (inverse-vol weighting) measured and kept.
- One component (quality gate) still untested, for want of point-in-time
  fundamentals — the same missing-data problem in a different costume.
- The surviving variant unfalsifiable on available data.

A strategy that cannot be validated is not a strategy yet. The account's
capital is better served by an index core than by a number nobody can check.

---

## "Runners keep running" — measured and rejected, 23 September 2026

Tested on the local cache (141 names + SPY, daily 2010-01-04 → 2026-09-21) before
acting on a momentum screen. Signals point-in-time; forward returns at 21/63/126
sessions; every conditional number compared against the unconditional mean of all
available names **on the identical dates**.

### H1 — stock gained >=20% in 21 sessions: NOT REAL

| | Conditional | Unconditional (same dates) |
|---|---|---|
| 1m hit rate | 57.9% | 56.7% |
| **6m hit rate** | **64.6%** | **65.6%** |
| 6m p10 / p90 | −20.0% / +67.2% | −14.3% / +28.8% |

**The hit rate is the finding: a runner is LESS likely than an average name to be
up six months later.** The large mean spread (+13.8% at 6m) is a wider
distribution, not a shifted one — one fat right tail.

The tail is the universe. The signal fires 627× on TSLA, 570× AMD, 435× NFLX,
428× MU, 386× NVDA — names selected into the list *because* they won:

| 6m spread | |
|---|---|
| All 141 names | +13.77% |
| Drop top-10 lifetime winners | **+1.57%** |
| Drop top-20 | **−0.53%** |

**Removing 10 of 141 names erases 89% of the effect.**

Tradable version, monthly rebalance, 5bp round trip:

| | CAGR | Sharpe | maxDD |
|---|---|---|---|
| Runner basket | 23.2% | **0.71** | −41.0% |
| Equal-weight same 141 names | 15.1% | **1.05** | −21.6% |
| SPY | 12.6% | 0.90 | −34.1% |

It beats SPY on return and loses to *equal-weighting the same universe* on Sharpe,
at double the drawdown. That is leverage on a rigged universe, not alpha. Costs
(~15bp/yr) were never the issue.

Statistics stated honestly: 9,670 observations across only 2,702 dates with ~6:1
overlapping windows → **effective n ≈ 190 monthly blocks**, not ~9,700. The
1-month spread's CI straddles zero.

### H2 — within 2% of the 252-day high: REAL, WRONG SIGN

Spreads −0.33% / −0.60% / −0.82% at 1/3/6m, CIs excluding zero at all three.
Standalone it predicts slightly *lower* forward returns.

**This independently corroborates the same day's cross-sectional backtest**, where
deleting the 52-week filter improved the strategy. Two unrelated methods — daily
conditional forward returns, and a monthly cross-sectional portfolio — reached the
same conclusion. That finding is now solid rather than suggestive.

### H3 — top-decile 3-month relative strength: UNDETERMINED, leaning noise

6m spread +4.00%, but hit rates are *identical* to baseline (66.5% vs 66.3%), the
median spread is half the mean, and winner-removal takes +4.00% → +0.70%. Its
basket Sharpe (1.24) is the one number that survives on its own terms, but with
~80% of the raw spread gone without the hand-picked winners, that Sharpe cannot be
attributed to the signal rather than the universe.

### Why this is recorded rather than retried

Multiple testing was counted, not ignored: 3 hypotheses × 3 horizons × 3 splits =
27 headline comparisons, where ~1.4 false positives at p<0.05 are expected. The
only results surviving both their CI and the winner-removal test are H2's negative
spreads.

The one test that would change the verdict is the one this project cannot run: the
identical code on a point-in-time universe **containing delisted companies**.
Robinhood serves none (inactive_instruments / missing_instruments), as established
earlier the same day. Cheaper secondary bar: a spread whose **median and hit rate
both** beat the date-matched baseline. None of the three clear it.

**Decision: no momentum or relative-strength screen from this universe informs a
position.** Three sweeps on 22-23 September — technical compression, fundamental
quality, and momentum — produced one name worth research (DG) and three measured
negative results. The negative results are the durable output.
