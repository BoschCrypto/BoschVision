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

---

## Applying the findings to the code — and a correction, 25 September 2026

### The defaults never changed (fixed)

FINDINGS.md said on 2026-09-23 that the regime gate and the 52-week filter were
"deleted from the specification." They were not. `GatedMomentumParams` still had
`use_regime_gate: bool = True` and `use_52w_filter: bool = True`, so every
`hf-bot xbacktest` run for the next day used the **known-worse** configuration.

Defaults are now off, the ablations are inverted to test *re-adding* each filter
(`with_regime_gate`, `with_52w_filter`, `with_both_filters`) so the burden sits on
a component to earn re-entry, and `tests/test_xsection.py` asserts the defaults
directly so this cannot silently revert.

Default spec now, 15.6y / 188 rebalances:

| | return | CAGR | maxDD | Sharpe |
|---|---|---|---|---|
| **gated_momentum (default)** | **1043.0%** | 16.9% | −31.9% | 0.88 |
| buy & hold SPY | 491.8% | 12.1% | −34.1% | 0.75 |
| with_52w_filter | 657.2% | 13.9% | −31.3% | 0.78 |
| with_regime_gate | 512.0% | 12.3% | −31.9% | 0.75 |
| with_both_filters | 352.0% | 10.2% | −31.3% | 0.66 |

Re-adding either filter still costs return and Sharpe. Those verdicts hold.

### CORRECTION — "inverse-vol weighting survived" was wrong

On 2026-09-23 this file recorded that inverse-volatility weighting "earned its
place" on Sharpe in both sub-periods (0.84 vs 0.79; 0.53 vs 0.52). **Those numbers
were measured with both filters ON.** With the filters off — the spec as it now
stands — the result does not replicate:

| | 2012-02 → 2018-12 | 2020-02 → 2026-09 | Full period |
|---|---|---|---|
| inverse-vol | 175.4% · **0.99** · −23.6% | 222.0% · 0.86 | 1043% · 0.88 |
| equal weight | 181.3% · 0.96 · −25.3% | 277.4% · **0.90** | 1328% · **0.89** |

Inverse-vol wins Sharpe in the first half, loses it in the second, and the
full-period gap (0.88 vs 0.89) is inside noise. **Revised verdict: UNDETERMINED,
not "survived."**

It is retained as the default on a narrower claim than before — drawdown control,
not return: −23.6% vs −25.3% in the first half and a lower worst case in both.
That is a risk preference, stated as one, rather than a measured edge.

### The methodological lesson — ablations are not independent

Removing two components **changed the verdict on a third.** A one-at-a-time
ablation measures a component *conditional on every other component's current
setting*, so a verdict expires the moment any other component changes.

> **CHECK:** after changing any default, re-run the full ablation set and re-read
> every prior component verdict. A verdict from a configuration that no longer
> exists is not evidence about the configuration that does.

This is why the spec is not "tuned" further from here. Each of these runs is a
comparison on a survivorship-biased 141-name universe, the interactions are real,
and continuing to search the component space until the number improves is
data-mining with extra steps. The absolute figures remain uncreditable (see the
23 September entry); only the internal comparisons carry weight.

---

## 2026-09-26 — The ranking cannot be compressed into a position I can afford

**Question.** The account has ~$1,289 cash and a $439 named-satellite reserve. The
cross-sectional engine produces a 20-name book. Does the measured edge survive
compression into 1-3 names — the only form a $439 satellite could take?

**Answer: no. Concentration destroys the risk-adjusted edge and the apparent
gain is a second-half artifact.**

Method: monthly (21 trading day, non-overlapping) rebalances over the cached
universe, 2011-01-04 → 2026-09-21, 188 rebalances, equal weight, **no costs** —
which favours the concentrated variants, so any deficit they show is real. SPY is
date-matched over the identical rebalance dates.

| strategy | total | Sharpe | maxDD | worst month |
|---|---|---|---|---|
| top-1 | 7122.4% | **0.79** | **−56.0%** | −33.1% |
| top-3 | 5035.5% | 1.01 | −30.2% | −21.1% |
| top-5 | 3508.9% | 1.08 | −24.2% | −23.2% |
| top-20 | 1488.9% | **1.17** | −18.4% | −14.8% |
| SPY (date-matched) | 500.6% | 0.93 | −23.3% | −18.0% |

Sharpe falls monotonically as the book concentrates, and **top-1's 0.79 is worse
than simply owning SPY (0.93)**. The 7122% headline is not skill; it is the same
signal levered by volatility.

**The split-half test is what settles it:**

| | top-1 | top-3 | top-20 | SPY |
|---|---|---|---|---|
| first half (94 mo) | 76.2% / **0.38** | 208.8% / 0.71 | 206.4% / 1.06 | 116.7% / 0.93 |
| second half (94 mo) | 3999.7% / 1.10 | 1563.2% / 1.27 | 418.6% / 1.27 | 177.2% / 0.94 |

In the first half **top-1 lost to the index outright** — 76.2% against SPY's
116.7%, with a −47.2% drawdown. All of its lifetime edge comes from the second
half, which is the 2018-2026 semiconductor/AI run: precisely the regime that this
universe's survivorship bias inflates most, because the universe is today's
survivors. top-3 also fails the first half (0.71 vs 0.93).

**Only the diversified top-20 beats SPY on Sharpe in both halves** (1.06, 1.27).
Diversification is not a tax on this strategy — it *is* the strategy. The edge
lives in the cross-section, not in the top name.

### What this forbids
- **No single-name expression of this ranking.** A $439 bet on the #1-ranked name
  is a strategy measured at a *worse* Sharpe than the index, and it is the same
  error already recorded against Novy-Marx gross profitability: taking a
  cross-sectional factor and betting it on 1-3 names is not the tested strategy.
- MU currently ranks **#1** on 12-1 momentum (+476.9% as of 2026-09-21) **and
  prints Wednesday 2026-09-30.** The engine's top name carrying a binary event is
  a coincidence, not a confluence — and per the above, #1 is the rank whose
  measured Sharpe is worst.

### Caveats, stated not estimated
- Survivorship bias is unfixed and unfixable here; it inflates every momentum row
  above and the concentrated ones most.
- No cost model. Adding costs penalises top-20 more than top-1, narrowing but not
  closing a 1.17-vs-0.79 Sharpe gap that already ignores them.
- The cache ends **2026-09-21**; a 12-1 signal with a skip month barely moves in
  three sessions, but the ranking is not as-of-Friday.
- **The current top-20 is not actually diversified**: 6 of 20 are semis
  (MU, INTC, AMD, AMAT, LRCX, KLAC) and 4 are refiners/energy (VLO, MPC, PSX,
  SLB). ~50% of the book sits in two clusters. The historical result was measured
  on the *rule*, not on this particular book, and the rule's diversification
  benefit is weaker right now than the table implies.

---

## 2026-09-28 — Two corrections and one live divergence

### 1. My look-through calculation was omitting VTI (method error)

This morning I reported NVDA look-through as **4.5%**. That was computed as direct
NVDA + NVDA-inside-VGT, **ignoring NVDA inside VTI** — and VTI is the largest
holding at $750. Corrected, with VTI's NVDA weight at 5.5/6.5/7.5%:

| | NVDA look-through |
|---|---|
| as reported this morning (wrong) | 4.5% |
| corrected, VTI @ 5.5% / 6.5% / 7.5% | 6.38% / **6.72%** / 7.06% |

Still well under the 12% cap, so no action followed from the error — but a
look-through cap computed on a subset of the funds that hold the name is not a
cap. **Every look-through number must include every fund held.** VTI's own weight
is an estimate and must be verified from Vanguard's holdings, not assumed.

### 2. The Oct-8 VTI tranche is not risk-neutral

The scheduled second $750 VTI tranche has been carried as a neutral core add. It
is not. Portfolio $2,197.49, of which cash $1,289.51 (58.7%):

| | now | after the tranche | cap |
|---|---|---|---|
| tech look-through (VTI tech @ 34%) | 18.8% | **30.4%** | 35% |
| NVDA look-through (VTI NVDA @ 6.5%) | 6.72% | **8.94%** | 12% |
| free cash after the $439 satellite reserve | $850.51 | **$100.51** | — |

At a 38% VTI tech weight the tranche puts tech look-through at 33.1%, inside the
35% cap with ~2pp to spare. The tranche is affordable but it consumes most of the
remaining sector headroom and nearly all genuinely free cash. **It should be
decided on that basis before 2026-10-08, not waved through on the day.** VTI is a
total-market fund; the total market is roughly a third technology.

### 3. Live divergence: memory is being sold, NVDA bought, two days before MU prints

Intraday 2026-09-28 ~10:15am ET, against the 09-25 closes:

| memory / storage | | broad semis | | energy |
|---|---|---|---|---|
| SNDK −4.73% | STX −3.61% | SMH −1.40% | AVGO −0.65% | XLE +0.70% |
| MU −3.37% | INTC −4.82% | TSM −1.14% | TXN −0.91% | VLO +0.56% |
| | | **NVDA +3.33%** | QCOM −7.09% | MPC +0.22% |

NVDA is the only semiconductor up, while the memory and storage complex is down
3.4–4.7% and SPY is −0.43%. A candidate mechanism: NVDA guided gross margin down
~300bp on memory cost and named the memory oligopoly as taxing the AI buildout, so
an expectation of **falling** DRAM/NAND prices relieves NVDA and hurts MU. Today's
tape is that trade running in NVDA's favour.

**I cannot confirm the cause** — the news hosts are proxy-blocked and QCOM's −7.09%
is a large move with no explanation I can source, which is itself a reason not to
over-read the pattern. Treat the mechanism as a hypothesis that Wednesday's MU
guide will test, and log the alternative: this may simply be de-risking into the
print, which carries no information about its outcome.

---

## 2026-09-28 — The momentum book with real frictions: it belongs in a tax-advantaged account, and dies in this one

**Question.** Saturday's finding was that only the diversified top-20 book carries
the edge. Robinhood's fractional shares make a 20-name book implementable at
$1,289. Does it survive spread, T+1 settlement in a cash account, and the tax on
its turnover?

**Answer: no, not in the taxable account. VERDICT — DO NOT FUND IT HERE.**

Method: `hf_trading_bot/bookstudy.py`, 188 monthly rebalances, 2011-01 → 2026-09,
$1,289.51 start. Sells execute at close *i*, and because proceeds settle T+1 the
buys execute at close *i+1* — rotated capital sits in cash for one session, which
is the actual cash-account constraint. Realised gains are taxed at each rebalance
and the tax is paid out of the account. **Measured annual turnover: 6.6x.**

### Each friction added one at a time

| | final $ | CAGR | Sharpe | cost paid | tax paid |
|---|---|---|---|---|---|
| frictionless control | 20,483 | 19.3% | 1.17 | — | — |
| + T+1 settlement | 20,582 | 19.3% | 1.10 | — | — |
| + 20bp spread round trip | 18,556 | 18.6% | 1.07 | $539 | — |
| **+ short-term tax @ 27%** | **8,661** | **12.9%** | **0.81** | $333 | **$2,818** |

Settlement is nearly free in return but costs 0.07 of Sharpe and widens the
sampled drawdown from −18.4% to −22.3%: being out of the market one day per
rebalance adds risk without adding return. Spread is minor — 20bp costs 0.7pp of
CAGR. **Tax is the whole story: $2,818, more than twice the starting capital and
8.5x the transaction cost.** It removes 5.7pp of CAGR and 0.26 of Sharpe.

### Against the honest benchmark

SPY buy-and-hold defers its tax indefinitely, so the comparison must be
after-tax terminal wealth: SPY $7,810, or **$6,832** after a single 15% long-term
tax on the gain. The taxed monthly book ends at **$8,661** — it wins on dollars by
27%, and **loses on Sharpe, 0.81 against 0.86.**

### The split-half kills it

| | first half (94 mo) | second half (94 mo) |
|---|---|---|
| top-20 monthly, taxed 27% | 113% / **0.79** | 216% / 0.85 |
| top-20 monthly, tax-free | 190% / **1.05** | 396% / 1.11 |
| SPY buy & hold | 126% / **1.00** | 168% / 0.81 |

**Taxed, the book lost to SPY in the first half on both dollars (113% vs 126%)
and Sharpe (0.79 vs 1.00).** Its entire advantage is second-half — the
survivorship-inflated regime. **Tax-free, it beats SPY in both halves** (1.05 vs
1.00, then 1.11 vs 0.81). The tax is what converts a strategy that works in both
regimes into one that works in one.

### Rebalance frequency does not rescue it

| taxable | final $ | CAGR | Sharpe | turnover |
|---|---|---|---|---|
| monthly @27% | 8,661 | 12.9% | 0.81 | 6.5x |
| quarterly @27% | 6,878 | 11.4% | 0.83 | 3.6x |
| semiannual @27% | 6,526 | 11.0% | 0.84 | 2.5x |
| annual @15% (long-term gains) | 7,953 | 12.9% | 0.99 | 1.5x |

Slowing down cuts the tax rate but discards the signal — 12-1 momentum decays in
weeks, so an annual hold is a different, weaker strategy. The annual arm's 0.99
Sharpe and −8.7% drawdown are **not trustworthy**: n=15, and the drawdown is
sampled once a year, so it measures how rarely the portfolio was looked at rather
than how gently it behaved. Drawdowns are not comparable across these rows for
the same reason.

### Caveats that survive
- **Survivorship bias is unfixed** and inflates every momentum row more than SPY.
  It is a larger effect than any friction measured here.
- Drawdowns are sampled at the rebalance frequency, so all of them understate the
  true intra-period figure; the monthly rows are the only ones roughly comparable.
- 20bp is an assumption, not a measurement. Robinhood fractional orders route as
  market orders; the real cost is unmeasured. At 100bp the tax-free CAGR falls to
  15.5% / Sharpe 0.91, so the conclusion is not sensitive to it.
- A $1 minimum per fractional order is satisfied at ~$64 per name, but 20 positions
  rebalanced monthly means ~20 approvals a month and 20 short-term tax lots
  compounding — an operational load this ledger has not priced.

### What follows
The strategy is real and it is in the wrong wrapper. **It is worth funding only
inside a tax-advantaged account** (a Roth IRA: 18.6% CAGR, Sharpe 1.07, beats SPY
in both halves). In the taxable Robinhood cash account the measured result is
worse risk-adjusted than doing nothing, so the $439 satellite reserve and the
Oct-8 tranche should not go here.

---

## 2026-10-01 — MU beat and fell. Two of my own methods were wrong.

**The print.** MU reported FY26 Q4 on 2026-09-30 pm: **EPS $33.42 against $31.50
consensus, a 6.1% beat**, verified. On 2026-10-01 the stock went **1065.11 →
1048.26, −1.58%**.

### The beat is fully priced — and the beat is shrinking

| report | EPS beat | stock move |
|---|---|---|
| 2025-06-25 | +18.6% | −0.98% |
| 2025-09-23 | +8.6% | −2.82% |
| 2025-12-17 | +25.1% | +10.21% |
| 2026-03-18 | **+41.9%** | **−3.78%** |
| 2026-06-24 | +24.3% | **+15.74%** |
| 2026-09-30 | **+6.1%** | −1.58% |

**MU has beaten EPS in all six quarters on record here, and the stock fell on
four of them.** Correlation between beat size and price move is **+0.15 on n=6**,
which is nothing. A 41.9% beat produced −3.78%; a 24.3% beat produced +15.74%.
An EPS beat is not a reason to expect a move in either direction.

Separately: the prior five beats ran 8.6 / 18.6 / 24.3 / 25.1 / 41.9%. **This
quarter's 6.1% is the smallest of the six.** Estimates are catching up to the
memory upcycle, so the surprise factor is fading. That is a real deceleration
signal, independent of price.

### Error 1 — my gap model was built from a magnitude-selected sample

On 2026-09-26 I told the principal to expect **±7% with a fat tail to ±18%**. I
derived it from "the 4 largest overnight gaps per year" in `data/bars/MU.csv`,
reasoning that MU prints four times a year so the counts matched.

**They do not match.** The four largest gaps in a year are not the four earnings
gaps — they are crises and shocks, and I had already noted that four of the top
twenty were March 2020 COVID days *while using the sample anyway*. I built a
predictor out of the right tail and presented it as a central expectation.

Computed correctly on the five **verified** prior earnings dates:

| | my estimate | correct method | realised |
|---|---|---|---|
| mean abs move | 7.76% | 6.71% | — |
| **median abs move** | **6.82%** | **3.78%** | — |
| this print | — | — | **1.58%** |

The mean survived by luck; the median — the number a person actually plans
against — was off by 1.8x, and the realised move was barely above a typical
0.87% day.

**NEW RULE: an event-study distribution must be built from the event dates,
obtained from `get_earnings_results`, never from a magnitude-selected sample.**
This is a new instance of Mode 1 in `knowledge/process/error-modes.md`: I
selected on the outcome variable and then used the selection as a forecast.

### Error 2 — the HYG credit tripwire is measured on the wrong series

Today Robinhood reports HYG `previous_close` **77.21** but
`adjusted_previous_close` **76.8684** — a **$0.3416 distribution** going ex on
1 October.

| | |
|---|---|
| today's move, raw closes | −0.74% |
| today's move, dividend-adjusted | **−0.30%** |
| vs my 09-24 baseline of 78.09 (unadjusted) | −1.86% |

**My baseline was an unadjusted price, so the tripwire conflates income being
paid out with credit stress.** HYG yields roughly 6% a year, about 0.5% a month,
so a "3% fall in under a month" threshold on an unadjusted series carries ~0.5pp
of guaranteed false signal every month — it would eventually fire on nothing but
distributions. The tripwire must use `adjusted_previous_close` or a total-return
series. Fixed in the routine.

### What could not be read, and therefore is not concluded

**The DRAM/HBM pricing guide — the only variable that actually mattered — is
unavailable.** The 2026-09-30 8-K returns **404** on `get_sec_filing`,
`get_sec_filing_facts` returns an **empty fact set** for it, and the news hosts
are proxy-blocked.

So whether MU's guide confirms or refutes the NVDA margin-transfer mechanism
(NVDA guided gross margin down ~300bp on memory cost and named the memory
oligopoly as taxing the AI buildout) is **UNRESOLVED**. A −1.58% move is too
small to carry that information, and the 2026-09-28 divergence — memory down
3.4–4.7% while NVDA was the only semi up — remains **untested**, not confirmed.
I will not infer the guide from the price.

**Still no position, and the case for one is weaker than before the print**, not
stronger: the beat decelerated, the reaction was negative, and the thesis
variable is unreadable.
