# Edge search: plan, fixed before any result was seen

Committed on 13 November 2026 before running any of the tests below. If a
result later looks good, check it against this file: anything not listed
here was added after the fact and should be discounted.

## Why the last strategy failed, and what that implies

On one-minute bars with holds of one to two hours the agents' agreement
predicted nothing (49% right against a 51.6% base rate) and fees of 20 basis
points a round trip were the whole loss. So the question is not "which
indicator" but **at what holding period, if any, does a simple signal earn
more than it costs**.

## Data

- Coinbase spot hourly closes, UTC. BTC-USD and ETH-USD from 1 Jan 2017.
- Spot stands in for the perpetual-style futures that would be traded.
- Decisions once a day at 00:00 UTC, using data up to that close. The
  position applies from that close onward (no same-bar look-ahead: a signal
  from day t's close earns day t+1's return).

## Costs, charged on every result

- 10 bps per side on every change of position (20 bps a round trip), plus
  2 bps slippage per side.
- Funding: a long pays 0.00125% an hour (about 11% a year, the long-run norm
  for crypto perpetuals). A short receives nothing. This is deliberately
  harsh on longs and ungenerous to shorts.
- No leverage. Position is +1, 0 or -1 times equity.

## Three periods

| Period | Dates | Use |
|---|---|---|
| Research | 2017-01-01 to 2022-12-31 | Look at everything. Choose here. |
| Validation | 2023-01-01 to 2024-12-31 | Confirm the choice. No re-choosing. |
| Holdout | 2025-01-01 to 2026-11-12 | Run once, on the final spec only. |

## Part 1: the edge map (description, not a strategy)

For horizons 1h, 4h, 12h, 1d, 3d, 7d, 14d, 28d: go with the sign of the
previous h's return, hold for the next h, non-overlapping. Report the mean
gross return per trade in basis points and its t-statistic, next to the 24
bps cost. Positive means moves continue, negative means they reverse. This
shows which timescales could carry a strategy at all.

## Part 2: candidate rules (14 variants, all daily)

| Family | Rule | Variants |
|---|---|---|
| A. Momentum, long or flat | Long if the past L-day return is above zero, else flat | L = 7, 14, 28, 56 |
| B. Momentum, long or short | Long if above zero, short if below | L = 7, 14, 28, 56 |
| C. Moving-average trend | Long if the close is above its N-day average, else flat | N = 20, 50, 100, 200 |
| D. Channel breakout | Long on a new N-day high; flat on a new N/2-day low | N = 20, 55 |

## Part 3: one non-trend idea

E. Reversal after an extreme day: if the day's return is beyond 2 standard
deviations (30-day), take the opposite side for one day.

## How a rule is chosen

1. On the research period only, rank the 14 variants by net Sharpe ratio
   averaged over BTC and ETH.
2. The winner must sit on a plateau: its neighbouring parameter values must
   also be positive. A lone spike is rejected and the next candidate taken.
3. Validation: the chosen rule must have a positive net Sharpe on both
   assets and must beat the matched-exposure benchmark (below). If it fails,
   **stop and report failure**. Do not go back and pick another.
4. Holdout: run once.

## Benchmarks

- Buy and hold the same asset, paying the same funding.
- **Matched exposure**: hold a constant fraction equal to the rule's average
  time in the market. A rule that is long 55% of the time is compared with
  always holding 55%. This separates timing skill from simply owning less
  of a volatile asset. "Timing value" is the rule's return minus this.

## What counts as an edge

All of: positive net return in the holdout; positive timing value in the
holdout; and a research-period t-statistic above 3.0 (not 2.0, because 14
variants were tried). Otherwise the verdict is "no demonstrated edge".

## Known weaknesses of this plan

- Two assets, both survivors that rose enormously. A long-or-flat rule on a
  rising asset looks good for a reason that has nothing to do with timing;
  the matched-exposure benchmark exists to catch that.
- Other coins (SOL, LTC, LINK, ADA, DOGE, AVAX) are run on the final rule as
  a robustness check only. They are today's listed survivors, so they
  flatter any long-biased rule and are not used to choose anything.
- The funding assumption is a constant, not history.
- Spot prices, daily decisions, no order book.
