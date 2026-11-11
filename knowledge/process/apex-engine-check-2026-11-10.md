# Apex engine check — 2026-11-10 (read-only; nothing was changed)

Scope: the three futures-simulator engines (source `src/lib/sim-core.ts`,
`src/lib/sim-engine.server.ts`, live state, bars, health, replay) and the
separate Alpaca PAPER automation (`btc_engine`, `automation_runs`, `trades`,
`adaptive_*`, `trading_settings`). Everything here is paper money.

## Futures simulator — no defect found

- State matches code. Sizing reproduces: BTC short risk 3 x 0.01 x 2,200.30 =
  $66.01, ETH short 7 x 0.1 x 95.23 = $66.66, i.e. 2% of a one-third sleeve.
- `sim_bars`: 5,099 hourly bars per asset over 5,098 hours — zero gaps.
- 200-day window (4,800 h): EMA400 (1h), EMA50 (8h) and daily EMA20/50 are converged.
- No look-ahead: signal on bar close, fill at next hourly open, daily filter uses the prior UTC day.
- Health 192/192 ok in 48 h; replay gate 13/13 pass; zero `error` rows in `sim_log`.

Known properties, not bugs:
1. No trailing stop. Both shorts: +$348 unrealized, $497 giveback to stops that
   still sit above entry. Both stopping out would turn +$348 into about -$133.
2. Carry is charged at 10%/yr on both sides. Logged funding is +4.7%/yr (BTC)
   and +4.4%/yr (ETH), so real shorts would be paid, not charged. Conservative.
3. The replay gate proves the sim matches itself, not the Python research.
   One behaviour to confirm against Python: on an opposite signal the engine
   exits and waits a full bar before it can re-enter (no stop-and-reverse).
4. Engines can oppose each other (10-29: sysa long 5 while btc_8h short 3).

## Alpaca paper automation — four things need correction

1. **Adaptive optimizer auto-applies curve fits.** 60 parameter changes in two
   weekly runs (11-01, 11-08), 53 applied, 180-day window. Average Sharpe
   -1.83 before, +5.41 after; 15 changes claim Sharpe > 5; maximum 72.76
   (ABNB rsi). AMD momentum lookback went to 60, then 120 a week later. ABBV,
   ABNB and AMT were "changed" to identical parameters two weeks running, so
   the first change did not stick or was overwritten.
2. **It was dead for ten weeks.** 1,297 of 1,435 runs failed with Alpaca 401
   from 2026-08-14 to 2026-10-23. Connection re-verified 10-26. The paper
   record is about two weeks long, not three months.
3. **Order bookkeeping is unreliable.** Nine equity buys and one sell are
   still `submitted`, never reconciled to fills. PLTR was bought twice (66 on
   10-29, 65 on 11-02) and AMD twice (24 on 11-03, 26 on 11-09) with no exit
   rows between. The VGT exit records +$136.27 but its own prices give
   (115.63 - 115.31) x 166 = +$53.12. On 11-04 an exit failed with Alpaca 403:
   166 VGT requested, 0 available, 166 held by a resting order.
4. **Drawdown breaker uses a stale high-water mark.** Settings HWM $100,046.30;
   highest equity on record $103,046.12 (2026-08-13). Latest equity $96,681.24
   is -6.2% from the true peak, -3.4% as the breaker sees it (limit 15%).

Paper BTC engine results so far: btc_1h_momentum two stop-outs, -$519.91 and
-$493.38.
