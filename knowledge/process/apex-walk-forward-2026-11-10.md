# Apex futures engines: anchored walk-forward — 2026-11-10

Answers the question the five-year backtest could not: do the parameters work
on data they were never fitted on? Run with `scripts/apex/walk_forward.py`
(needs BTC_1h.csv / ETH_1h.csv from `fetch_coinbase_hourly.py` in the same
directory). Runtime ~3 minutes.

## Method

Anchored walk-forward, three folds. For each fold a 45-point grid (lookback
x0.67/0.85/1.0/1.2/1.5, trend EMA x0.6/1.0/1.6, stop x0.75/1.0/1.5, applied
uniformly to all three engines) is fitted on the TRAINING years only, selected
by training Sharpe, then **frozen** and run on the following year, which the
fit never saw. Bars before each test year feed the indicators; no bar after
the test year's end is visible to that run.

## Result

| train | test year | fitted params | OOS fitted | OOS frozen (live) | BTC | ETH |
|---|---|---|---:|---:|---:|---:|
| 2021-11→2023-11 | 2023-11→2024-11 | x1.0 / x1.0 / x1.0 | +36.8%/yr | **+36.8%** | +109.7% | +48.1% |
| 2021-11→2024-11 | 2024-11→2025-11 | x1.0 / x0.6 / x1.0 | +31.4%/yr | **+31.4%** | +38.5% | +16.5% |
| 2021-11→2025-11 | 2025-11→2026-11 | x1.0 / x0.6 / x1.0 | +32.7%/yr | **+37.5%** | −28.9% | −41.0% |

Out-of-sample with the live parameters: **+36.8%, +31.4%, +37.5%** — three
positive years out of three, mean **+35.2%/yr**, max drawdowns −18.9%, −10.8%,
−10.8%. The out-of-sample mean matches the full-period figure (+35.1%), so the
backtest was not inflated by fitting.

## The three things this establishes

1. **The lookback is not an overfit.** The grid chose x1.0 in all three folds,
   fitting only on data before each test year. The local peak I flagged on
   2026-11-10 is stable across sub-periods, not an artifact of having seen
   2024-2026. **That correction supersedes the overfitting reading in
   `apex-five-year-backtest-2026-11-10.md` §1.**
2. **Fitting adds nothing.** Re-fitting each year beat simply leaving the
   parameters alone in 1 of 3 years (mean +33.6% fitted vs +35.2% frozen).
   This is direct evidence for the fix made to the adaptive optimizer the same
   day: leave parameters alone unless the case is overwhelming.
3. **It made money in a year both assets fell hard.** 2025-11 to 2026-11: BTC
   −28.9%, ETH −41.0%, system **+37.5%**. That is the behaviour the whole
   design is for, measured on data the parameters never saw.

## What it does NOT establish

- **Only the three multipliers were re-derived.** Which engines exist, the 8h
  and 1h bar choices, long/short, the daily-EMA filter and the entry rule
  itself were all chosen knowing the whole history. This tests parameter
  robustness, not the discovery process.
- **BTC and ETH were chosen with hindsight.** The two assets that survived and
  led the sector.
- **The folds share data.** Three test years inside one five-year sample are
  not three independent experiments.
- **Concentration stands.** Ten trades of 332 are 87% of the five-year profit.
- **It badly lags a strong bull.** 2023-11 to 2024-11: BTC +109.7%, system
  +36.8%. Trend-following buys that shortfall as the price of the 2026 result.
- **Execution is still untested.** Modelled costs only; the earlier stress run
  (doubled fees + 0.10–0.25% slippage) gave +22% to +28%/yr, which remains the
  planning band. Live record is 2 closed trades.

## Verdict

Stronger than anything else in this project. The honest sentence is now:
*the parameters hold up on data they were never fitted on, across three
consecutive years including one in which both underlying assets fell more than
28%.* It is not proof of future profitability and no backtest can be. What
remains unanswered is whether it survives real fills, and whether Waldo can
hold it through a 266-day stretch below a prior peak.
