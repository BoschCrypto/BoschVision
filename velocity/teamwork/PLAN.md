# Can a team of agents reading different data find high-probability setups?

Fixed 14 November 2026, before any of the new data was examined. Decisions
taken with the account owner beforehand: target holding period **1 to 7
days**; if nothing clears the bar in section 6, **report and stop**.

## 1. The question, and why the last attempt does not answer it

The existing five-agent system averaged the votes of QUANT and CHARTIST.
Both are functions of recent price, so their agreement was close to a
restatement of one input, and it was measured as worth +1.8 bps on ETH and
0 on BTC (`velocity/shortterm/REPORT.md` section 5).

Two things change here:

- **Different inputs.** Six agents, six data sources, three of which the
  old system never saw: implied volatility, cross-asset risk appetite, and
  market breadth.
- **Conjunction, not averaging.** A setup qualifies only when several
  agents independently agree. The old system blended a continuous score;
  this one intersects conditions.

The honest framing of what must be shown: in the final test of the previous
study the gross edge before any cost was **+0.58 bps per trade**. The signal
predicted nothing. The question is not whether trading can be made cheaper.
It is whether a combination of different data sources predicts anything at
all.

## 2. Data

| Agent | Input | Source | Span |
|---|---|---|---|
| TREND | price vs its own trailing average | Coinbase spot 1-minute | Nov 2023 - Nov 2026 |
| FLOW | traded volume vs its own norm | same bars | same |
| CROWD | perpetual funding rate | Deribit hourly | Apr 2019 - Nov 2026 |
| FEAR | DVOL implied-volatility index | Deribit | Nov 2023 - Nov 2026 |
| MACRO | VIX | IBKR, CBOE index, daily | 5 years |
| BREADTH | share of Coinbase USD pairs above their own 20-day average | Coinbase daily | Nov 2023 - Nov 2026 |

Decisions are made on the **daily close, 00:00 UTC**, from data already
published at that moment. Forward returns are measured from the next day's
open. Non-crypto data (VIX) is only used from the prior US session's close,
so a weekend decision uses Friday's figure.

## 3. What each agent computes

Every reading is converted to a **percentile against its own trailing 365
days**, so the six are comparable and no threshold is hand-set in price
units. All windows are finite and backward-looking.

| Agent | Reading | "Bullish" condition | Reason it might carry information |
|---|---|---|---|
| TREND | close vs 72-hour average, in standard deviations | rank >= 0.5 | the only input the old system had |
| FLOW | 24-hour volume vs 30-day median | rank >= 0.6 | a move on real participation is more likely to continue than one on none |
| CROWD | 7-day average funding | rank <= 0.3 | longs are not crowded; less fuel for a liquidation cascade |
| FEAR | DVOL level | rank <= 0.4 | options are not pricing a shock; calm regimes trend |
| MACRO | VIX level | rank <= 0.5 | global risk appetite is on; crypto is a risk asset |
| BREADTH | share of USD pairs above their 20-day average | rank >= 0.5 | the move is broad, not one coin |

Bearish conditions are the mirror image. Both directions are tested.

## 4. What is measured

For BTC and ETH separately, and for each horizon of **1, 3 and 7 days**:

1. **Each agent alone.** Split its reading into quintiles; report the mean
   forward return of each quintile against the unconditional mean.
2. **Each pair of agents.** 15 pairs.
3. **Conjunctions by count.** Mean forward return when 2, 3, 4, 5 and 6
   agents agree on a direction.
4. **The best conjunction found**, stated with the number of conditions
   searched so its significance can be discounted.

Count of conditions examined, declared in advance: 6 agents x 5 quintiles +
15 pairs + 5 agreement counts = **50 per asset per horizon**, so 300 in
total. A t-statistic of 2 among 300 tests is expected by chance roughly 15
times; this is why the bar in section 6 is set where it is.

## 5. Statistics

Forward returns sampled daily over a 7-day horizon overlap, so ordinary
standard errors would be roughly sqrt(7) too small. Every t-statistic
treats a **calendar week as one cluster**. Ranges come from a block
bootstrap over weeks.

Costs: a round trip on Coinbase perpetual-style futures is **24 bps**
(10 bps fee each side plus half the spread and slippage, measured
13 Nov 2026). Every result is reported gross and net of that.

## 6. The bar for "we found something"

All four, or the answer is no:

1. Mean forward return **net of 24 bps** is positive.
2. Clustered t-statistic **above 3.0** (not 2.0, because 300 conditions are
   examined).
3. It holds for **both BTC and ETH**, same direction, same conditions.
4. At least **30 non-overlapping occurrences** per asset.

A result that clears all four is still only a candidate. It would then be
specified as a mechanical rule, run through the existing backtester with
real fills and the account's risk budget, and forward-tested on data that
does not exist yet.

## 7. Known weaknesses, stated now

- **The holdout is not pristine.** The 2025-26 window was used by the
  previous study. The contamination is specific (short-term *price* rules
  were shown to fail there) and says little about funding, volatility or
  breadth conjunctions, but it is not a clean test and will not be
  described as one. Only forward testing on new data can be.
- **Breadth is survivor-biased.** The Coinbase pairs are the ones listed
  today; coins delisted over the period are missing, which flatters any
  breadth measure.
- **VIX is a daily US figure** and does not trade at the weekend, while
  crypto does.
- **Two assets**, both of which rose enormously over the long run.
- Funding history comes from Deribit, a venue the account cannot trade on.
