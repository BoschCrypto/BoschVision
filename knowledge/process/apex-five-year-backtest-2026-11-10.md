# Apex futures engines: five-year backtest — 2026-11-10

Supersedes the "not answered" in `apex-replay-2026-11-10.md`. Waldo approved
the Coinbase download; 51,326 hourly bars per asset (2021-01-01 to
2026-11-10, 13 missing hours) were replayed through the parity-checked port
in `scripts/apex/simport.py`. Reproduce with `fetch_coinbase_hourly.py`, then
`run_five_years.py` and `stress_five_years.py` (run from a directory holding
the two CSVs).

## Result, frozen rules and costs, $10,000, 2021-11-10 to 2026-11-10

- $10,000 -> $45,031: +350.3%, **+35.1% a year**, daily Sharpe 1.33.
- Max drawdown -19.0%; longest stretch below a prior peak 266 days.
- 332 trades, 87 wins (26%), profit factor 2.13, average win +$723, average loss -$121, costs paid $7,390.
- 8-week windows: 39% negative, median +2.0%, worst -13.7%.
- Max gross exposure 1.63x equity.

| year | 2021 (from 11-10) | 2022 | 2023 | 2024 | 2025 | 2026 (to 11-10) |
|---|---:|---:|---:|---:|---:|---:|
| return | -3.2% | -1.4% | +94.6% | +45.4% | +24.0% | +34.6% |

By engine (net): btc_8h_ls +$9,904 (92 trades, PF 2.18), sysa_1h_long
+$10,918 (138, PF 1.75), eth_8h_ls +$12,512 (102, PF 2.90). Longs +$28,935,
shorts +$4,399. Top 10 trades made $28,921 of the $33,333 total.

Buy and hold over the same five years: BTC +11.5% (drawdown -77.2%), ETH
-55.2% (-81.4%). From 2021-07-20 instead: system +36.8%/yr, BTC +18.3%/yr.

## Stress tests (five-year CAGR / max drawdown)

| variant | CAGR | max drawdown | profit factor |
|---|---:|---:|---:|
| base | +35.1% | -19.0% | 2.13 |
| fee 0.12% + 0.10% slippage per fill | +28.2% | -21.2% | 1.78 |
| fee 0.12% + 0.25% slippage per fill | +22.0% | -23.9% | 1.53 |
| lookback x0.67 / x1.33 | +21.9% / +26.1% | -24.5% / -23.9% | 1.57 / 1.93 |
| trend EMA x0.6 / x1.6 | +34.7% / +35.1% | -20.5% / -19.1% | 1.93 / 2.31 |
| stop x0.75 / x1.5 | +43.8% / +23.2% | -23.2% / -14.5% | 1.98 / 2.16 |
| daily EMA x0.5 / x2 | +35.0% / +24.3% | -17.4% / -19.6% | 2.04 / 1.77 |

## Reading it

1. **In-sample.** The parameters were chosen on this history. The lookback
   sits on a local peak (both neighbours are 9-13 points lower), so some of
   the 35% is selection. Every neighbour stays profitable, 22% to 44%.
2. A realistic expectation from this evidence is the 22-28% band, which
   matches the audit's "20-25% a year". The audit's -30% to -40% drawdown is
   more cautious than the -19% to -24% measured here, and is the better
   planning number.
3. Dependence on a few trades: 10 of 332 trades are 87% of the profit.
4. The first 14 months (2021-11 to 2022-12) lost money while BTC fell 65%.
5. Fills are modelled at the stop price unless the hourly open gaps. Coinbase
   spot bars stand in for futures; the live sim uses Alpaca bars. On the live
   window the Coinbase replay makes the same trades within about $1 each.

Earlier verdict "unknown" is revised to: positive and fairly robust in
backtest over five years, unproven out of sample (live: 2 closed trades).
