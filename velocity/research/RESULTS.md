# Edge search: results (13 November 2026)

Run exactly as fixed in `PLAN.md`, which was committed before any test.

**Verdict: no demonstrated edge.** The chosen rule passed on research data
and failed validation on ETH. Per the plan the search stopped there. The
holdout (2025 to Nov 2026) has **not been run** and is still clean.

## 1. Where moves continue: the edge map

Go with the last move, hold for the same length. Gross basis points per
trade, before the 24 bps cost.

| Horizon | BTC 2017-22 | ETH 2017-22 | BTC 2023-24 | ETH 2023-24 |
|---|---|---|---|---|
| 1 hour | -0.8 | -0.4 | -0.7 | -0.6 |
| 4 hours | -1.7 | -0.6 | -2.1 | -0.4 |
| 12 hours | +10.5 | +26.4 | +0.8 | +0.1 |
| 1 day | -6.3 | +1.4 | +0.2 | +2.3 |
| 3 days | +36 | +69 | +34 | +3 |
| 7 days | +30 | +108 | +5 | +87 |
| 14 days | +315 | +651 | -108 | -162 |
| 28 days | +320 | +443 | +207 | +274 |

- **Under a day there is nothing to trade.** Following the last move earns
  about minus one basis point. This is why the one-minute bot lost: it was
  paying 20 bps a round trip for a signal worth less than zero.
- **From 3 to 28 days moves did continue in 2017-22**, by far more than the
  cost. Only the 14-day figure was statistically solid (t = 2.2 and 3.0).
- **In 2023-24 the effect was weaker and inconsistent**; at 14 days it
  reversed sign. None of the 2023-24 figures is distinguishable from zero.
- The 12-hour effect in 2017-22 (t = 4.6 on ETH) disappeared entirely.

## 2. The 14 rules on research data (2017-22), after costs

Every slow trend rule beat buy-and-hold on Sharpe ratio. The reversal idea
(E) did nothing. Ranking by Sharpe averaged over BTC and ETH:

| Rule | Avg Sharpe | Lowest t | Plateau |
|---|---|---|---|
| **D:20 channel breakout** | **1.56** | 2.9 | yes |
| C:20 above 20-day average | 1.49 | 3.0 | yes |
| A:14 momentum, long/flat | 1.46 | 3.2 | yes |
| C:50 | 1.41 | 3.3 | yes |
| A:28, A:7, D:55, A:56, C:100 | 1.18 to 1.35 | 2.5 to 3.2 | yes |
| B (long or short), all four | 0.80 to 1.02 | 1.1 to 2.4 | yes |
| C:200 | 0.91 | 2.0 | yes |
| E reversal | 0.15 | 0.3 | n/a |

Chosen by the plan's rule: **D:20**. Long on a new 20-day closing high, flat
on a new 10-day closing low. About 14 position changes a year, in the market
about 40% of the time.

Going short never helped: every long-or-short variant was worse than its
long-or-flat twin.

## 3. Validation (2023-24), rule locked

| | Rule CAGR | Sharpe | Max drawdown | Buy and hold | Timing value | t |
|---|---|---|---|---|---|---|
| BTC | +64.5% | 1.59 | 31% | +112.2% | +12.6% a year | 0.7 |
| ETH | +3.5% | 0.28 | 49% | +50.2% | **-14.5% a year** | -0.7 |

Timing value is the rule's return minus what you would have earned holding a
constant fraction equal to the rule's average exposure. On ETH it is
negative: the rule was in the market at the wrong times. **Validation
fails**, so the plan's step 3 applies: stop, report failure, do not pick
another rule.

On BTC the timing value is positive but nowhere near significant, and D:20's
research t-statistic on BTC was 2.9 against the plan's bar of 3.0.

## 4. What the rule actually is: crash insurance

Year by year, research and validation years only:

| Year | BTC rule | BTC hold | ETH rule | ETH hold |
|---|---|---|---|---|
| 2017 | +793% | +1,060% | +5,487% | +7,699% |
| 2018 | **-29%** | -75% | **+26%** | -83% |
| 2019 | +86% | +75% | 0% | -10% |
| 2020 | +134% | +262% | +249% | +425% |
| 2021 | +5% | +46% | +426% | +351% |
| 2022 | **-43%** | -68% | **-22%** | -71% |
| 2023 | +77% | +129% | +17% | +72% |
| 2024 | +52% | +96% | -8% | +32% |

It wins big in crash years (2018, 2022) and gives up a large share of every
bull year. Its good research-period score came from two crashes. That is a
different risk profile, not an ability to predict; in a period with no crash
(2023-24) it simply earned less than holding.

## 5. What this does and does not establish

- A fast signal on BTC or ETH at these fees is a dead end. Both the bot's
  own results and this map say so.
- Slow trend-following reduced crashes historically and has not shown
  timing skill out of sample.
- Nothing here was tested on the holdout. A future, differently-conceived
  idea can still use it once.

## 6. Caveats

Two assets, both long-run winners. Spot prices, daily decisions, constant
funding assumption, no order book. Fourteen variants were tried, so any
single t-statistic near 3 should be read as weaker than it looks.

## 7. Reproduce

```bash
python -m velocity.research.edge_search research   DIR
python -m velocity.research.edge_search validation DIR --rule D:20
```

`DIR` holds `BTC-USD_1h.csv` and `ETH-USD_1h.csv` from
`velocity.feeds.coinbase_history.fetch_candles(symbol, 3600, start, end)`.
