# Three-way test: plan, fixed before any funding data was pulled

Committed 13 November 2026, before downloading a single funding rate. The
question from the account owner: **of these three, which returns the most?**

1. Funding carry: hold the coin, short the perpetual, collect funding.
2. Funding extremes as a signal: trade against crowded positioning.
3. The 20-day breakout rule (D:20 from PLAN.md), used as risk control.

## Data

- Prices: the hourly Coinbase spot history already downloaded (BTC, ETH).
- Funding: the **longest public history obtainable** for BTC and ETH
  perpetuals is the primary series (BitMEX is expected to be it; Kraken
  Futures or Deribit if BitMEX cannot be reached). Whatever is used is named
  in the results.
- Sensitivity: Coinbase's own funding (Coinbase International, and the level
  on the US perpetual-style futures today), because Coinbase is where the
  account can trade. If Coinbase's funding is materially lower than the
  primary series, carry results are restated at Coinbase's level and the
  restated figure is the one that counts for carry.

## Periods (unchanged)

Research to 2022-12-31 (from the start of funding data, at least one year of
which is used only to warm up percentile ranks). Validation 2023-2024.
Holdout 2025-01-01 to 2026-11-12, run **once**, after every spec below is
locked, for all three strategies in the same command.

## Common accounting

Daily, at 00:00 UTC. No leverage beyond what is stated. Futures trades cost
12 bps a side (10 fee + 2 slippage). Anyone long a perpetual pays the actual
funding for that day; anyone short receives it (negative funding reverses
this). Returns are on total capital committed.

## Strategy 1: funding carry

- Hold 1 unit of the coin on spot, short 1 unit of the perpetual.
- Capital: two thirds buys the coin, one third sits as margin for the short.
  So funding is earned on two thirds of capital.
- Costs: spot 60 bps a side (Coinbase's entry-level spot fees are 50 to 90),
  futures 12 bps a side, on entry and exit. Each time price moves 25% from
  the last rebalance, pay both fees on 15% of the position (moving margin
  between the legs).
- 1a: always on. 1b: on only while the trailing 7-day average funding is
  above zero; out (earning nothing) otherwise.
- Choose 1a or 1b by research-period net annual return averaged over BTC and
  ETH. That choice is final.
- Ignored, and said so: day-to-day gaps between the perpetual and spot
  (they net out over time but make the real path bumpier), interest on idle
  margin, exchange failure.

## Strategy 2: funding extremes

- Signal: trailing 7-day average funding, ranked against its own previous
  365 days (past data only).
- 2a: short when the rank is 90% or higher, long when 10% or lower, else flat.
- 2b: the same at 80% and 20%.
- 2c: long when the rank is 20% or lower, else flat (buy fear only).
- Choose by research-period net Sharpe averaged over BTC and ETH. Final.

## Strategy 3: breakout as risk control

- D:20 exactly as in PLAN.md: long on a new 20-day closing high, flat on a
  new 10-day closing low. Nothing to choose.
- One change from PLAN.md, applied to make the three comparable: the long
  pays the **actual** funding for each day instead of a constant 11% a year.

## References, shown beside every result

- Buy and hold the coin on spot (no funding).
- Cash: approximate US Treasury-bill yields by year (2017 0.9%, 2018 1.9%,
  2019 2.1%, 2020 0.4%, 2021 0.1%, 2022 2.0%, 2023 5.1%, 2024 5.0%,
  2025 4.2%, 2026 4.0%). These are my estimates, not downloaded data.

## How "which returns the most" is answered

Net annual return after costs, with the worst peak-to-trough loss beside it:

1. on the **holdout**, which is the answer that counts, and
2. over the full history, for context.

Reported for BTC, ETH, and half in each. The winner is the highest holdout
return on the half-and-half portfolio. A strategy that does not beat cash on
the holdout is reported as not worth its risk, whatever its rank.

## Known weaknesses

- Funding history comes from an offshore venue the account cannot trade on;
  the US venue's own history is too short to test on. Hence the Coinbase
  restatement above.
- Strategy 3 already failed validation as a source of timing skill
  (RESULTS.md). It is included here as a risk-control choice at the owner's
  request, not as a rediscovered edge.
- Two assets, both long-run winners.
