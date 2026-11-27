# Decision 2026-11-10: fund the Roth, let the engines run

Waldo's call, after the walk-forward came back positive and after he reported
$12,000 in cash.

**Decided:** do not fund the futures system now. Let the simulator run on
paper. Revisit when the Roth is funded and the evidence gate is met.

## The reasoning, in one line

The 2026 Roth room expires 2027-04-15 and is worth roughly $128,000 at 65.
The futures system has no deadline. That asymmetry decided it.

## A correction I owe the record

On 2026-11-10 I told him "below $5,000 the system cannot execute its own
rules." That was overstated, and measuring it showed so:

| account | trades skipped (of 332) | median risk/trade | worst trade |
|---:|---:|---:|---:|
| $3,000 | 1 | 0.69% | 1.30% |
| $5,000 | 0 | 0.67% | 0.91% |
| $10,000 | 0 | 0.67% | 0.80% |

Target is 0.667% of equity per trade. **$5,000 executes correctly**; the real
floor is nearer $3,000. The only cost at small size is a fatter tail. The case
against funding now rests on the Roth deadline, untested execution, and the
$4,935 of non-reserve cash — not on sizing. `apex-go-live-criteria.md` should
be read with this correction: its "$5,000 sizing floor" is right as a target
and wrong as a hard constraint.

## Running, untouched

- **Futures simulator** — every 15 min. At 2026-11-10 06:35 UTC: equity
  $10,211.03 (+2.11%), 2 closed trades, 2 open shorts, max drawdown −3.14%,
  replay gate `pass`, zero health warnings in 24h.
- Futures health check-in (2x daily) and the weekly report — his choice to keep.

## Stopped

- The three Robinhood routines, disabled 2026-11-03 (credits). See
  `routines-halted-2026-11-03.md`.

## One live loose end

The **old** adaptive optimizer is still the published code, so it keeps firing
weekly against the Alpaca equity paper account (last runs 11-01, 11-08; next
~11-15) and will keep applying curve fits. The fix is committed in the Lovable
project but not published. Paper money, so no capital at risk — but each run
further degrades that system's record. Publishing is a one-click action Waldo
can take whenever; it cannot affect the futures simulator, which never imports
`adaptive.server.ts`.

## Next checkpoints

| when | what |
|---|---|
| this week | $7,500 into the 2026 Roth, government money market fund (not the sweep) |
| 2026-12-23 | 8-week review, ~10 closed trades — machinery check, not a verdict |
| ~2027-04 | ~30 closed trades; Gate B of the go-live criteria assessed |
| ~mid-2027 | Earliest sensible funding, with new money, starting as an execution test |
