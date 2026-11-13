# Short-term BTC/ETH strategy study: rules fixed before any result

Committed 13 November 2026 while the price data was still downloading. If a
number in the report looks good, check it against this file. Anything not
written here was decided after results were seen.

## 1. What wins

**Objective:** highest compounded return on one shared account, after every
trading cost, inside a fixed risk budget.

**Risk budget** (the account's existing WARDEN limits, from
`presets.perp_engine`, applied identically to every candidate):

| Limit | Value | Source |
|---|---|---|
| Starting equity | $5,000, one balance for BTC and ETH | existing |
| Risk per trade | 0.5% of equity lost if the stop is hit | existing |
| Largest position | 25% of equity (no leverage) | existing |
| Both assets open together | total risk at most 0.75% of equity, total position at most 37.5% | **new assumption**: 1.5 times one position, because BTC and ETH move together |
| Daily loss limit | 2% of the day's opening equity: flatten, no entries until the next UTC day | existing |
| Largest acceptable drawdown | 8%. A candidate that exceeds it out of sample is disqualified | existing |
| Size | whole contracts only: 0.01 BTC, 0.1 ETH | venue rule |

**Selection:** walk-forward on the first 80% of history. The winner is the
strategy family with the highest stitched out-of-sample compounded return on
the combined account, provided it is above zero, stays inside the 8%
drawdown and has at least 100 out-of-sample trades. If nothing is above
zero, the least bad is still frozen and tested so the record is complete,
and the verdict is "no strategy qualified".

**Final test:** the most recent 20%, run once on the frozen winner.

Any "most profitable" statement is limited to the strategies, instruments,
dates and costs listed here.

## 2. Venue, instruments, data

- **Venue:** Coinbase (the account's exchange).
- **Primary instrument:** Coinbase Derivatives perpetual-style futures,
  `BIP` (0.01 BTC) and `ETP` (0.1 ETH). Long and short.
- **Secondary:** Coinbase spot BTC-USD and ETH-USD. Long only. Modelled
  separately because its fees are nine times higher and it cannot short.
- **Price data:** Coinbase spot one-minute OHLCV, 13 Nov 2023 to 13 Nov
  2026, UTC, from the public Exchange API. Spot is used as the futures'
  price series (the futures settle to it); the futures' own history only
  starts in mid-2025. The gap between futures and spot is not modelled.
- Decisions on completed 5-minute bars. Context from completed 15-minute,
  30-minute and 1-hour bars only. Fills and stops are simulated on the
  one-minute bars.

## 3. Costs

| Cost | Futures | Spot | Measured or estimated |
|---|---|---|---|
| Taker fee, per side | 10 bps | 90 bps | measured (account fee tier, 13 Nov 2026) |
| Maker fee, per side | 9.5 bps | 50 bps | measured |
| Spread, full | BTC 0.73 bps, ETH 5.16 bps | assume same | measured, one snapshot of the futures book |
| Slippage beyond the spread | 0.5 bps a side; 1 bp on stop orders | same | **estimated** |
| Latency | a market order sent at a 5-minute close fills at the open of the next one-minute bar | same | modelled |
| Funding | hourly, from Deribit's history; a long pays, a short receives | none | proxy from another venue |

- A resting limit order fills only if price trades **through** it by at
  least one spread. A touch is not a fill.
- If a one-minute bar touches both the stop and the target, the stop is
  assumed to have come first.
- A stop that is gapped through fills at the bar's open, not at the stop.

## 4. Periods

- History: 13 Nov 2023 to 13 Nov 2026 (1,096 days). First 14 days warm up
  indicators.
- **Final test: 8 Apr 2026 to 13 Nov 2026** (the last 20%).
- **Research: up to 8 Apr 2026.** Walk-forward: choose on 270 days, test on
  the next 90, step 90. Seven test folds; the last is shorter.
- Positions are closed at every fold boundary. No trade spans two folds.
- The code loads only research-period data until the freeze file exists.

**Not pristine, disclosed:** earlier work in this repository already looked
at parts of the final period: the current five-agent system was replayed on
15 Aug to 13 Nov 2026, and a 20-bar breakout rule was measured on 5-minute
to 1-hour bars over Nov 2024 to Nov 2026 in aggregate. Both lost money. The
three new families below have never been run on any data.

## 5. Candidates (30 configurations, all listed)

Shared definitions, all from completed bars only: `ATR` is the average true
range of the last 24 five-minute bars. The 1-hour trend is the sign of the
last completed hourly close minus the average of the last T hourly closes.
A setup whose stop would be closer than 0.15% or further than 1.5% from
entry is skipped. One position per asset at a time.

### F1. Trend pullback (8)

*Why it might work:* intraday trends resume after shallow pullbacks, and
entering on the pullback buys a better price with a tight stop.
*When it should fail:* when hourly direction does not persist, which an
earlier measurement on this data suggests (following the last 1-4 hour move
earned about minus one basis point).

- Setup: 1-hour trend up (T hours). A 5-minute close at least `k` ATR below
  the highest high of the last 36 five-minute bars, still above the hourly
  average. Mirrored for shorts.
- Trigger: within 6 bars, a 5-minute close above the previous bar's high.
  Market order, fills at the next one-minute open. If no trigger in 6 bars
  the setup expires and is logged.
- Stop: the pullback's low minus 0.25 ATR.
- Exit: limit at `R` times the risk; or a market exit after 6 hours.
- Grid: T in {24, 72}, k in {1.5, 2.5}, R in {1.5, 3}.

### F2. Volatility breakout (8)

*Why:* volatility clusters; a tight range tends to be followed by a larger
move, and the break shows its direction.
*When it should fail:* in ranging markets, where most breaks reverse and
each false start pays the full cost.

- Setup: the last 4 hours' high-to-low range is at most `c` times the median
  4-hour range of the previous 7 days.
- Order: resting stop orders 0.1 ATR above the range high (buy) and below
  the range low (sell), live for 12 bars; the first one hit cancels the
  other. Unfilled orders expire and are logged.
- Optional filter: only take the side that agrees with the 24-hour trend.
- Stop: the middle of the range.
- Exit: a stop that trails `m` ATR behind the best price since entry,
  updated on 5-minute closes; or a market exit after 8 hours.
- Grid: c in {0.6, 0.8}, filter in {on, off}, m in {3, 5}.

### F3. Failed-breakout fade (8)

*Why:* breaks of an obvious high or low often run stops and trap late
entrants; when price closes back inside, those traders have to exit.
*When it should fail:* in strong trends and on news, where the break is real.

- Level: the highest high (lowest low) of the previous N hours.
- Setup: a 5-minute bar trades at least 0.1 ATR beyond the level, and within
  3 bars a 5-minute close is back inside it. Known at that close.
- Trigger: market order against the break, next one-minute open.
- Optional filter: skip if the 24-hour trend points the way of the break.
- Stop: the extreme of the break plus 0.25 ATR.
- Exit: limit at `R` times the risk; or a market exit after 4 hours.
- Grid: N in {4, 12}, filter in {on, off}, R in {1, 2}.

### S. Simpler rules, which are allowed to win (6)

- S1 Hourly trend: each hour, hold the side of the T-hour trend. T in
  {24, 72}; long-or-short and long-or-flat. No stop; sized at the position
  cap. (4)
- S2 Channel breakout: long on a new 20-bar closing high, flat on a new
  10-bar low, on 15-minute and on 1-hour bars. (2)

### References, never selected

Buy and hold at 25% of equity. Cash.

### Baselines: the system as it runs today

Run through the existing engine on one-minute bars with the same account
and the same fees and spread: (a) the current five agents, (b) QUANT alone,
(c) CHARTIST alone. Its orders fill at the bar's close plus its own latency
model, which differs slightly from the next-bar-open rule above; stated in
the report.

Within each fold, each family uses the configuration that did best on that
fold's 270 training days. One set of parameters for both assets. Per-asset
results are reported, and whether an asset-specific choice would have
differed.

## 6. Does ARBITER earn its place

On the research period, the current agents run in shadow (no trading). At
every 5-minute close with a non-zero fused score, record the score, each
agent's vote and confidence, and whether they agree. For each, compute what
a trade in the score's direction would have made with WARDEN's own stop (2
units of horizon volatility), target (3 units) and a time limit of twice the
horizon, at the costs above. These are hypothetical and overlapping; they
are kept apart from account results and their uncertainty is computed with
days as clusters.

Questions, each answered with a number:
1. Does waiting for |score| of 0.28 or more improve the average outcome?
2. Does CHARTIST's vote predict returns once QUANT's vote is accounted for?
3. Does either agent's confidence predict returns after costs? Including
   the case of CHARTIST at 95-100% confidence with a vote under 0.10.

## 7. Stress tests on the frozen strategy

Double the slippage. Delay every entry by one 5-minute bar. Move each
parameter one grid step. Remove the five most profitable trades. Results by
asset, by month, by market condition (hourly trend up, down, flat; high and
low volatility). The round-trip cost at which the edge is zero. A range for
average net profit per trade from a block bootstrap over weeks. A result
that rests on one asset, one month, or limit fills is flagged.

## 8. What will be said if nothing works

If no family is above zero out of sample, or the frozen winner loses on the
final test, the report says so, leaves no blank filled with a hopeful
number, and shows which cost or condition removed the apparent edge.
