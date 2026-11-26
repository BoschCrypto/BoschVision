# Apex futures: what fees and slippage will actually cost — 2026-11-10

The one unmeasured input in the whole analysis. Researched and, where possible,
measured. Scripts: `scripts/apex/cost_scenarios.py`, `stop_excursion.py`.

## 1. LIVE SPREAD — measured, not estimated

IBKR quote on the Coinbase nano Bitcoin future (BIT, Nov-2026, COINDERIV),
2026-11-10, status REALTIME:

| | |
|---|---|
| bid / ask | **74,795 / 74,815** |
| spread | $20 on BTC price = **0.0267%** |
| cost of crossing once | **0.0134%** |
| depth | 241 contracts bid, 28 ask |
| volume today / open interest | 19,002 / 115,986 |

**The spread is tight and the book is deep relative to this system.** It trades
3–11 contracts; 28 contracts rest at the ask. Market impact is effectively
zero. Spread is a real cost but a small one.

## 2. FEES ARE PER CONTRACT, NOT A PERCENTAGE — the model's structural error

Coinbase Derivatives charges **$0.10 per side per contract** for BIT (fee
schedule effective 2026-01-26; broker commission is extra, quoted between
$0.25 and $0.49 per side). The simulator charges **0.06% of notional**. Those
are different shapes, and the gap moves with price:

| era | model charges per contract | reality (~$0.35) |
|---|---:|---|
| 2022, BTC $20,000 | $0.120 | **under-charged ~3x** |
| 2024, BTC $60,000 | $0.360 | about right |
| 2026, BTC $75,000 | $0.450 | about right |
| ETH, 0.1 @ $2,100 | $0.126 | **under-charged ~3x** |

The ETH engine is under-charged in every era, because 0.1 ETH is only ~$210 of
notional. Re-running the five years with real dollar fees:

| fee per side per contract | CAGR | profit factor | max DD |
|---|---:|---:|---:|
| $0.10 (exchange only) | +36.8% | 2.23 | −18.3% |
| $0.35 (exchange + cheap broker) | +34.2% | 2.10 | −18.9% |
| $0.59 (exchange + typical broker) | +30.9% | 1.96 | −19.2% |
| $1.00 (pessimistic) | +27.0% | 1.80 | −22.6% |
| *0.06% of notional (what the sim used)* | *+35.1%* | *2.13* | *−19.0%* |

Adding the measured spread on top of $0.35 fees: **+33.2%/yr**.

## 3. FUNDING — the model is conservative by 5 to 15 points a year

From the project's own `funding_history` (Deribit, 43,669 hours since
2021-11-10):

| | average | median | hours positive |
|---|---:|---:|---:|
| BTC | **+4.54%/yr** | +0.18%/yr | 60.0% |
| ETH | **+2.59%/yr** | +0.02%/yr | 54.2% |

The simulator charges **10%/yr to both sides**. So longs are over-charged by
about 5.5 points, and shorts — which should *receive* roughly 4.5%/yr — are
charged 10% instead, a ~14.5 point error in the conservative direction. Note
the mean is far above the median: funding is spike-driven, which is income for
a short and expensive for a long.

## 4. STOP FILLS — the one genuinely unmodelled risk

Of 134 stop-outs in the five years, the simulator filled **100% exactly at the
stop price** and gapped through on **none**. Real stops become market orders.
How far price actually travelled past the stop inside the same hour:

| | past the stop |
|---|---:|
| 25th pct | 0.12% |
| median | **0.40%** |
| 75th pct | 0.85% |
| 90th pct | 1.63% |
| 99th pct | 3.81% |
| worst | 4.48% |

Capturing 25% of that excursion as realised slippage costs about **0.169% per
stop-out**, roughly 3–4 points of annual return. This is the largest remaining
unknown and only a live fill will settle it.

## 5. WHAT TO EXPECT

| component | effect on CAGR |
|---|---|
| base (sim's own assumptions) | +35.1% |
| real per-contract fees ($0.35–$0.59) | −1 to −4 pts |
| crossing the measured spread | −1 pt |
| stop-fill slippage (est. 25% of excursion) | −3 to −4 pts |
| funding charged correctly | **+2 to +5 pts** (model is too harsh) |
| **expected** | **+25% to +31%/yr** |

This converges with the independent audit's 20–25% and the earlier stress-test
band of 22–28%. **Plan on 25%, not 35%.** Drawdown planning stays at −30% to
−40%, which is worse than anything measured.

## 6. TWO STRUCTURAL FINDINGS THAT MATTER MORE THAN THE COSTS

1. **The monthly BIT contracts expire** — Nov 27, Dec 25, Jan 29 on the IBKR
   ladder. The simulator assumes a continuous position and would need a roll
   every month, with its own cost and slippage. **Coinbase also lists
   perp-style nano futures (BIP, 0.01 BTC, expiry Dec 2030, hourly funding,
   24/7 bar a Friday 5–6pm ET break)**, which matches the simulator's design.
   Use BIP, not BIT, or the backtest does not describe what is being traded.
2. **A nano ETH contract could not be confirmed.** IBKR returned nothing for
   the Coinbase Ether futures searches tried. The ETH engine produced the
   largest share of backtest profit (+$12,512 of $33,333), so if the 0.1 ETH
   contract is not available at this venue, **a third of the system cannot be
   traded as designed.** Verify before anything else.

## Sources

Coinbase Derivatives fee schedule eff. 2026-01-26; CFTC filing 2025-32 (nano
Bitcoin Perp Style Futures); Coinbase help, US perpetual-style funding
mechanism; Optimus Futures and Discount Trading commission pages (both
possibly stale); live IBKR quote and futures ladder, 2026-11-10. Broker
commission at IBKR specifically was **not** confirmed.
