# Three-way test: results (13 November 2026)

Run as fixed in `PLAN2.md`, committed before any funding data was pulled.
The holdout was run once, after the specs below were locked.

**Answer: the 20-day breakout rule returned the most, in the holdout and
over the full history. In the holdout none of the three beat cash.**

## Data actually used

- Prices: Coinbase spot, hourly, BTC and ETH.
- Funding, primary: **Deribit**, hourly from 30 April 2019 (the longest
  series reachable; BitMEX refused connections from this region and Kraken
  keeps one year).
- Funding, cross-check: Binance, every 8 hours from January 2020.
- The first year of funding only warms up the percentile ranks, so every
  strategy is measured from 8 May 2020.

## Specs locked on research data (May 2020 to Dec 2022)

| Choice | Result on research data | Locked |
|---|---|---|
| Carry: always on, or only while funding is positive | +4.3% a year vs -1.1% | **Always on.** Stepping out and back in cost more in fees than the negative funding it avoided. |
| Funding signal: 90/10, 80/20, or buy fear only | Sharpe -0.16, +0.09, +0.75 | **Buy fear only.** Shorting crowded longs lost 13% and 5% a year: crowded markets kept rising. |
| Breakout | nothing to choose | D:20 as before |

## Results, half in BTC and half in ETH, after all costs

| | Holdout: Jan 2025 to Nov 2026 | | Full history: May 2020 to Nov 2026 | |
|---|---|---|---|---|
| | A year | Worst loss | A year | Worst loss |
| **20-day breakout** | **+2.7%** | 39% | **+25.4%** | 41% |
| Funding carry | +0.9% | under 1% | +3.8% | 3% |
| Buy fear only | -10.8% | 37% | +12.2% | 39% |
| *Buy and hold* | *-15.9%* | *60%* | *+42.3%* | *76%* |
| *Cash (approx.)* | *+4.2%* | *0%* | *+3.1%* | *0%* |

Validation (2023 to 2024), for completeness: breakout +30.7%, buy fear only
+12.7%, carry +4.9%, buy and hold +101.9%, cash +5.2%.

By asset in the holdout: breakout +3.6% BTC and -0.2% ETH; carry +1.9% and
-0.2%; buy fear only -10.4% and -13.6%.

## Reading it

**Breakout.** Highest return in both windows, so it is the answer to the
question asked. In the holdout crypto fell about 16% a year and the rule
made about 3%: it stayed out of most of the decline. That is the crash
insurance described in RESULTS.md doing its job, not a forecast. It still
carried a 39% worst loss and its holdout return is not statistically
distinguishable from zero (Sharpe 0.23). Its full-history 25% a year comes
from being long a rising asset about half the time.

**Carry.** Almost no losses, almost no return. Funding averaged 7% a year
over the full history but only 4.0% (BTC) and 1.2% (ETH) in the holdout,
and it is earned on two thirds of capital, less the cost of getting in and
out. It returned less than Treasury bills in validation and in the holdout.
It only pays in bull markets, when funding is high.

**Buy fear only.** Lost in the holdout. Very low funding marked the start
of declines as often as the end of them.

## Cross-checks

- **Binance funding, same specs, holdout:** breakout +2.6%, carry +1.7%,
  buy fear only -6.3%, buy and hold -13.0%. Same order, same conclusion.
- **Kraken, last 12 months:** BTC funding averaged 3.7% a year.
- **Coinbase US today:** 4.4% a year on BTC and 5.3% on ETH. In line with
  the series used, so the carry figures are not restated. At those rates
  carry on Coinbase would earn roughly 3% a year on capital before entry
  and exit costs: still below cash.

## Caveats

- Funding comes from offshore venues the account cannot trade on.
- Carry's worst loss is understated: day-to-day gaps between the perpetual
  and spot, and the risk of the exchange failing, are not modelled.
- Two assets, both long-run winners, which flatters anything that is long.
- The breakout rule failed validation as a source of timing skill in
  RESULTS.md, and nothing here reverses that.
- The cash yields are my approximations, not downloaded data.
- The holdout has now been used. Any further idea tested on 2025-26 is no
  longer a clean test.

## Reproduce

```bash
python -m velocity.research.funding_data FUNDING_DIR
python -m velocity.research.three_way research   PRICE_DIR FUNDING_DIR
python -m velocity.research.three_way validation PRICE_DIR FUNDING_DIR --carry 1a --signal 2c
python -m velocity.research.three_way holdout    PRICE_DIR FUNDING_DIR --carry 1a --signal 2c
```

Add `--source binance` for the cross-check.
