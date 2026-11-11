# Apex futures engines: replay over the stored bars — 2026-11-10

Question asked: profitability of the engines over the past five years.
**Not answered.** The system stores 214 days of hourly bars (2026-04-11 on),
the Python research backtest (`st_engine`) is in neither repository, and the
download of five years of Coinbase bars was denied by the session's permission
check after the host had been allowed. What follows is the most that could be
measured.

## Method

`scripts/apex/simport.py` is a line-for-line Python port of `runSim` in the
Lovable project's `src/lib/sim-core.ts`. Parity check: seeded as a first run
at 2026-10-28 16:30Z it reproduces the live record exactly — the same two
sysa stop-outs (-73.76, -68.12), the same two short entries (BTC 3 @
81,256.30, ETH 7 @ 2,357.60) and cash of 9,858.13.

(The live engines first evaluated the bar closing 2026-10-29 00:00, one 8h bar
later than `started_at` implies. Seeded at 14:31Z instead, the ETH short would
have entered at 2,332.06 and been stopped on 11-04 for -69.90.)

## Result: all three engines, $10,000, frozen rules and costs

| start | days | return | trades | wins | profit factor | max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-20 | 174 | +29.58% | 29 | 7 (24%) | 2.74 | -11.15% |
| 2026-06-15 | 148 | +20.94% | 30 | 6 (20%) | 2.13 | -11.63% |
| 2026-07-10 | 123 | +23.72% | 25 | 5 (20%) | 2.56 | -11.39% |
| 2026-08-10 | 92 | +26.03% | 20 | 4 (20%) | 3.02 | -11.44% |

Start 2026-07-10 by engine: btc_8h_ls +626.89 realised (5 trades, 1 win),
sysa_1h_long +485.28 (17 trades, 2 wins), eth_8h_ls +808.66 (3 trades, 2
wins), plus +451.36 open. Longs made +2,051.80; shorts lost -130.97.
Twenty-one start times 8h apart from 07-06 end between $12,275 and $12,411.
Buy and hold over 07-10 to 11-10: BTC +18.5%, ETH +21.7%.

## Why this is not evidence of an edge

1. One regime. BTC ran from about 63,000 to 86,000 inside this window, which
   is the best case for a trend follower. No 2022-style decline, no long chop.
2. Three trades are the result. From 07-10 the three best trades made
   $2,651.76; the other 22 lost $730.94 combined.
3. Probably in-sample. The parameters were chosen by research that had this
   period available. The only out-of-sample stretch is since 10-28: realised
   -141.87, open +345.
4. The earliest start rows have partly unconverged daily EMAs.

## Files in the Lovable project actually read

In full: `src/lib/sim-core.ts`, `src/lib/sim-engine.server.ts`. Not read:
`sim-replay.server.ts`, `sim-health.server.ts`, `meta.server.ts`,
`gated-shadow.server.ts`, `strategy-engine.server.ts`, `backtest.server.ts`,
the adaptive optimizer, the screener, and the rest of a repository of well
over 100 files. The paper-automation findings of 2026-11-10 were diagnosed
from database tables, not from code.
