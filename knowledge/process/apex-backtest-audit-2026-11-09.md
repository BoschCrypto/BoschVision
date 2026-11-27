# Audit: how profitable is the Apex system? — 2026-11-09

Waldo asked for a verdict on profitability "based on all data you can access."
This is that verdict, with the queries that produced it.

## The answer

**The data cannot support a profitability claim, and three of its headline
numbers are measurably wrong.** Not "unproven" — wrong, in ways that can be
checked from inside the database itself.

Worse, the two bodies of evidence do not overlap. The 847 backtest trades are
**US equities**. The live simulator trades **BTC and ETH futures**. They share
no asset, no timeframe and no engine. So the backtests say nothing at all about
the thing that is actually running.

## 1. The selection funnel — 314 in, 49 out, zero failures kept

| stage | count |
|---|---:|
| distinct symbols through the screener | **314** |
| screener result rows / universe runs | 1,570 / 17 |
| symbols surviving into the watchlist | **49** |
| symbols with backtests | **49** |
| backtest trades | 847 |
| **rows in `symbol_backtests` with negative Sharpe** | **0** |

The minimum Sharpe across all 49 is AAPL at 0.20. A genuine test of three
strategies across 49 names produces losers. There are none, because the
screener chose the 49 **before** they were backtested. That is selection on the
dependent variable, and it is the same error this project already measured on
its own universe (momentum continuation: 64.6% vs 65.6% unconditional).

## 2. Two tables disagree by 2x on the risk of the SAME backtest

`strategies` and `symbol_backtests` both hold momentum_90d on NVDA:

| | CAGR | Sharpe | max drawdown | win rate |
|---|---:|---:|---:|---:|
| `strategies` | 48.28% | **1.29** | **−27.07%** | 53.3% |
| `symbol_backtests` | 45.99% | **0.66** | **−11.90%** | 52.9% |

CAGR and win rate agree. **Sharpe differs by a factor of two and drawdown by
2.3x.** Two tables in one database cannot agree on the risk of an identical
backtest, which means at least one risk number in this system is wrong, and
nothing downstream of either can be trusted until that is resolved.

Note which way the error runs: `symbol_backtests` — the table with 49 rows and
no failures — is the flattering one.

## 3. `symbol_backtests.max_drawdown` is the worst single TRADE, not a drawdown

In **12 of 22** rsi_mean_reversion rows, `max_drawdown` equals `avg_loss_pct`
to the cent (EXC −1.28/−1.28, SNPS −2.42/−2.42, ABBV −0.73/−0.73, SYK, EMR,
EFX, HON, HPE, LMT, SPGI, VZ ...). In **4 rows** it is exactly **0.00**, with a
**100% win rate** (VMC, PG, MU, FCX).

A strategy spanning 2021-01 to 2026-10 — which contains the 2022 bear market —
cannot have a zero drawdown. The field is measuring the worst closed trade and
calling it drawdown. It therefore ignores consecutive losses and all open-trade
mark-to-market, which is most of what a drawdown is.

Everything built on it is void: VMC's Sharpe 4.48, PG's 3.29, and the strategy's
22-row average Sharpe of 1.88. The same strategy in the honest table scores
Sharpe 1.01 (NVDA), 0.61 (QQQ), 0.68 (VGT).

## 4. The profit factors are not attainable

Computed directly from the 847 trades:

| strategy | trades | win rate | profit factor | avg win | avg loss | best trade |
|---|---:|---:|---:|---:|---:|---:|
| rsi_mean_reversion | 239 | 88.3% | **16.95** | +8.18% | −3.64% | +34.2% |
| momentum_90d | 312 | 42.3% | **5.18** | +22.2% | −3.15% | **+566.5%** |
| sma_crossover | 296 | 58.1% | **4.95** | +20.2% | −5.65% | +155.8% |

Professional trend systems run a profit factor of **1.2 to 2.0**. Above ~3
sustained is, in practice, always a modelling error. 16.95 means $16.95 won per
$1 lost.

Read the payoffs: momentum_90d claims a **7:1** win/loss ratio at a 42% hit
rate; sma_crossover claims **3.6:1 at 58%**. A 58% win rate with a 3.6:1 payoff
would be among the best systems ever published. Finding it on 16 hand-picked
symbols is the tell.

**Neither table has a cost column.** The equity backtests appear to charge no
commission, no spread and no slippage. The futures simulator does model costs
(0.06%/side plus funding) — so the better-specified system is the one with no
track record, and the flattering one is the system with no costs.

## 5. rsi_mean_reversion has no stop, and has not yet met its loss

239 trades over 5.8 years, 28 losers, **worst single loss −11.77%**. The 88%
win rate with capped-looking losses is the signature of selling volatility
without paying for the tail: it wins small and often until it doesn't.

VZ is the preview — 90.9% win rate, average win +5.31%, average loss
**−11.77%**. The one loser is 2.2x the typical winner. That ratio gets worse,
not better, in a real downtrend.

## 6. What the simulator actually has

To its credit, the simulator is the honest part of this system. The replay gate
has run 12 times and the latest status is **pass**, meaning live execution
matches a replay of the frozen rules on stored bars. Costs are modelled. The
stated expectation — ~20–25%/yr, drawdowns −30% to −40%, ~28% win rate, 34% of
8-week windows negative — is a believable trend-following profile, unlike
anything in §4.

What it has achieved, as of 2026-11-09 (12 days):

- Closed trades: **2, both losses.** Realised profit factor **0.00**, win rate 0%.
- Realised −$141.87; fees+carry $9.34 of that.
- Equity $10,209.23 (**+2.09%**), entirely from two **open shorts** (+$351.10).
- Max drawdown −3.14%; current drawdown −0.48%.

If the true win rate is 28%, losing the first two is a 51.8% event. **n=2 is not
evidence either way.** Open P&L is not profitability.

## Verdict

**Unknown, and the backtests do not move it off unknown.** Specifically:

1. The backtests cover a different asset class from the live system. Zero transfer.
2. The surviving 49 were chosen before being tested, and contain no failures.
3. Two tables disagree 2x on the risk of the same backtest.
4. One drawdown field is provably the worst trade rather than a drawdown.
5. Profit factors of 5–17 with no cost model are not results.
6. The live system's realised record is 0 for 2.

The BoschVision side is already further along and its answer was mostly no: the
regime gate lost on real bars (352% / Sharpe 0.66 vs SPY 491.8% / 0.75 over
15.6y), concentration destroyed the edge monotonically, and the taxable version
lost to SPY outright (12.9% / 0.81 vs 18.6% / 1.07 untaxed). And even those
cannot be compared to SPY honestly, because a point-in-time universe with
delisted names is unavailable — the standing blocking task.

## What would change this verdict

- Rebuild `symbol_backtests.max_drawdown` as a peak-to-trough equity drawdown and re-rank. Expect most Sharpes above 1.5 to collapse.
- Add costs (commission + spread + slippage) to the equity backtests. Expect the profit factors to fall toward 1.
- Backtest the **314** screened symbols, not the 49 survivors, and keep the losers.
- For the futures system, wait for the 2026-12-23 review, and treat ~30 closed trades as the floor for a first read — not 2.

Until then the only defensible statement is: **the simulator runs its own rules
faithfully, and nobody yet knows whether those rules make money.**
