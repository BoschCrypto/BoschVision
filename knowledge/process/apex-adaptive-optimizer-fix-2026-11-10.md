# Apex paper automation: adaptive optimizer fix — 2026-11-10

Change made in the Lovable project (id 92a77664-86f9-4619-b799-81ff4fb9dfda),
commit `70767b1e40d749dcd37241c77034fc001131ec90`, by one message to the
Lovable agent (2.7 Lovable credits). Files: `src/lib/adaptive.server.ts` and a
new `src/lib/adaptive.server.test.ts`. Build passed; 53 tests passed. No
database rows were changed and the adaptive job was not run.

## What was wrong (read from the code)

1. A variant qualified with 2 closed trades, so near-identical trades gave a
   huge Sharpe (72.76 on ABNB).
2. Trades from the 260 warm-up bars were counted and annualised over 180 days.
3. Any improvement counted as better: no margin, no second-period check.
4. Rank fell back to the best variant's in-sample Sharpe.
5. The `watchlist_symbols` write was never checked, so `applied: true` was
   logged regardless.

## What the fix does

- Window 180 days -> 1,095 days; only trades ENTERED in the window count.
- A change is applied only if both live and challenger params have >= 12
  in-window trades (>= 5 in each half), the challenger beats live by >= 0.3
  Sharpe, its Sharpe is <= 3, and its mean trade return is higher in both
  halves. Otherwise the live params are kept and the reason is logged.
- Rank fallback uses the live params' own Sharpe (clamped to +/-3) when there
  are enough trades, else a neutral 0.
- A failed write is logged as not applied.

Expected effect: rsi_mean_reversion (about 2 trades a year per symbol) will
essentially never be re-tuned; momentum and SMA names can be, rarely.

## Not done

- **Not published.** If the scheduled job runs from the published app, the
  fix takes effect only after a publish. Not done without Waldo's say-so.
- **Past changes not reverted.** 30 symbols had a change applied; 8 still
  differ from their pre-optimizer params (all momentum lookbacks): AAPL 90->45,
  VGT 90->120, NVDA 90->120, GS 90->45, QQQ 45->120, AMD 90->120, PLTR 90->60,
  LRCX 90->45. The other 22 were already overwritten back.
- **Second writer untouched.** `universe-promote.server.ts` upserts screener
  params, `live_enabled: true` and rank onto every non-manual watchlist row on
  each universe refresh, which is what erased the 22 changes above and would
  also undo an adaptive demotion. Which job owns `params` is a product decision.
