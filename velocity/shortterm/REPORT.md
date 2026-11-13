# Short-term BTC/ETH strategy study: report

13 November 2026. Rules of the study were fixed in `PLAN.md` before any
result existed; the freeze was committed before the final period was loaded.

## 1. Verdict

**No strategy qualified. Nothing here has earned a paper-trading trial.**

| | |
|---|---|
| Selected strategy | By the plan's rule the least-bad family was frozen so the record is complete: **hourly trend, 72 hours, long or flat** (`S1-T72-lf`). It did not qualify in research. |
| Assets that qualified | None. BTC and ETH both lost in research and in the final test. |
| Final test (8 Apr to 13 Nov 2026, 219 days, run once) | **-13.29%** net |
| Maximum drawdown in the final test | **13.84%** (the budget allows 8%) |
| Trades in the final test | **309** (137 BTC, 172 ETH) |
| Paper-trading trial earned | **No** |

Why: across 30 configurations in five families, the average trade made
between -3 and +18 basis points before costs, and a round trip on Coinbase's
futures costs 22 (BTC) to 26 (ETH). On the 5-minute-to-1-hour horizon there
was no setup whose gross edge came within 20 bps of paying for itself.

"Most profitable" is not a claim this study can make. Scope of everything
below: Coinbase perpetual-style futures on BTC and ETH, priced from Coinbase
spot one-minute bars, 13 Nov 2023 to 13 Nov 2026, at the account's entry-tier
fees, for the 30 configurations listed in `results/registry.csv`.

## 2. Comparison

One $5,000 balance shared by BTC and ETH. Same fees and spread for every row.

### Final test: 8 Apr to 13 Nov 2026

| System | Scope | Net return | Max drawdown | Avg net / trade | Profit factor | Trades | Time in market | Costs |
|---|---|---|---|---|---|---|---|---|
| **Frozen strategy** (hourly trend) | BTC | -3.85% | | -16.1 bps | 0.63 | 137 | 43% | $222 |
| | ETH | -9.44% | | -29.6 bps | 0.59 | 172 | 48% | $445 |
| | **Combined** | **-13.29%** | **13.84%** | **-23.6 bps** | **0.60** | **309** | 56% | **$667** |
| Current five agents | BTC | -3.11% | | -20.3 bps | 0.35 | 102 | 4% | $170 |
| | ETH | -4.81% | | -33.6 bps | 0.26 | 222 | 7% | $182 |
| | **Combined** | **-7.92%** | **7.92%** | **-29.4 bps** | **0.30** | **324** | 7% or more | **$351** |
| QUANT alone | Combined | -7.92% | 7.92% | -36.1 bps | 0.10 | 230 | 4% or more | $266 |
| CHARTIST alone | Combined | -7.92% | 7.92% | -31.0 bps | 0.30 | 260 | 9% or more | $319 |
| Buy and hold, 18.75% in each | Combined | -2.66% | | | | 2 | 100% | |
| Cash | | 0% | 0% | | | 0 | | |

The three versions of the current system all end at -7.92% for the same
reason: each lost about 8% in its first six to nine weeks, at which point
its own drawdown rule shrank every order below one contract and it stopped
(last trade 15 Aug, 8 Aug and 1 Aug). The number is where the brake is, not
a measure of the strategy. Per trade they were the worst rows in the table.

BTC fell 0.8% and ETH 10.1% over the window.

### Research out-of-sample span: 23 Aug 2024 to 7 Apr 2026

| System | Net return | Max drawdown | Avg net / trade | Profit factor | Trades | Costs |
|---|---|---|---|---|---|---|
| Hourly trend, walk-forward (re-picked each fold) | -8.51% | 18.60% | | | 681 | |
| Frozen configuration run straight through | -16.65% | 25.17% | -12.9 bps | 0.82 | 668 | $1,469 |
| Current five agents | -7.70% | 7.86% | -28.4 bps | 0.36 | 349 | $350 |
| QUANT alone | -7.70% | 7.87% | -23.3 bps | 0.42 | 401 | $441 |
| CHARTIST alone | -7.34% | 8.19% | -27.3 bps | 0.42 | 322 | $342 |
| Buy and hold, 18.75% in each | -1.86% | | | | | |

Charts: `results/equity_final.png`, `results/equity_research_oos.png`.

### Spot instead of futures

The frozen rule on Coinbase spot (long only, 90 bps taker): **-67.9%** in the
final test, 316 trades, $3,452 in costs. Spot is not usable at this cadence.

## 3. How the current system works, traced in code

1. **Candle to tick.** `mind.run_live` fetches each closed one-minute candle
   and calls `Engine.on_tick` (`engine.py`).
2. **Stops first.** `executor.manage` checks the open trade's stop and target
   before any agent runs; then WARDEN's hard stops (`must_flatten`).
3. **Agents.** QUANT (`agents/quant.py`) and CHARTIST (`agents/chartist.py`)
   each publish a strength in [-1, 1] and a confidence in [0, 1] to the
   blackboard. A **vote** is strength x confidence x freshness.
4. **Score.** `Arbiter._fuse`: the weighted average of the non-zero votes,
   weights QUANT 0.45, CHARTIST 0.40, SENTINEL 0.15.
5. **Decision.** `Arbiter.decide`: enter only if |score| >= 0.28 and at least
   two agents vote the same way; SENTINEL can veto; then `Warden.review`.
6. **Order and fill.** `PaperExecutor.open` fills at the tick price plus
   spread, slippage and a latency allowance, and writes the position and
   balance back to the blackboard.

**The screenshot, reproduced through that code:** QUANT -0.22, CHARTIST
+0.08, SENTINEL silent: (0.45 x -0.22 + 0.40 x 0.08) / 0.85 = **-0.0788**.
Below 0.28, one agent on each side: WAIT. Confirmed by running the real
`Arbiter` on those inputs.

**Why CHARTIST shows 100% confidence with a +0.08 vote.** Its confidence is
`0.25 + 0.45 x |structure| + 0.30 x (a break in the last 40 bars)`. It
measures how much structure is present, not how strongly that structure
points one way. Its strength is a sum of three terms that can cancel
(structure +0.30, distance from VWAP -0.25). So "sure and nearly neutral"
is a normal state: CHARTIST was at 95% confidence or more on **a third of
all samples**.

## 4. What was tried (30 configurations)

Full specification in `PLAN.md` section 5; every result in
`results/registry.csv`. Decisions on 5-minute closes, context from completed
hourly bars, fills simulated on one-minute bars.

| Family | Idea | Configs | Gross per trade | Cost | Best config, research period | Walk-forward out of sample |
|---|---|---|---|---|---|---|
| F1 Trend pullback | Buy the dip in an hourly uptrend | 8 | -2 to +1 bps | 24-25 | -85.7% | -83.4%, 0 of 7 folds up |
| F2 Volatility breakout | Trade the break of a tight 4-hour range | 8 | +1 to +5 | 26 | -47.2% | -36.2%, 0 of 7 |
| F3 Failed-breakout fade | Fade a break that closes back inside | 8 | -3 to -1 | 24-25 | -32.7% | -23.0%, 0 of 7 |
| S1 Hourly trend | Hold the side of the 24/72-hour trend | 4 | +11 to +15 | 24-25 | -25.0% | **-8.5%**, 3 of 7 |
| S2 Channel breakout | 20-bar high in, 10-bar low out | 2 | +6 to +18 | 24-25 | -12.6% | -14.8%, 2 of 7 |

- The three short-horizon families have essentially **zero** gross edge.
- The slower rules show +11 to +18 bps gross, but they are long-biased and
  BTC rose 19% over the span; buy and hold beat all of them.
- **Common parameters:** in every family the best configuration was the same
  for BTC alone and for ETH alone. Nothing suggests asset-specific tuning
  would help.
- **Missed entries:** setups that expired or were rejected are logged with
  their reason and with what they would have made.

### Walk-forward folds for the winning family

| Fold | Chosen on | Tested on | Days | Picked | Trades (BTC / ETH) | Net |
|---|---|---|---|---|---|---|
| 1 | 27 Nov 2023 - 23 Aug 2024 | 23 Aug - 21 Nov 2024 | 90 | T72 long/flat | 104 (39 / 65) | +2.43% |
| 2 | 25 Feb - 21 Nov 2024 | 21 Nov 2024 - 19 Feb 2025 | 90 | T72 long/flat | 102 (36 / 66) | -6.55% |
| 3 | 25 May 2024 - 19 Feb 2025 | 19 Feb - 20 May 2025 | 90 | T72 long/flat | 126 (65 / 61) | -6.34% |
| 4 | 23 Aug 2024 - 20 May 2025 | 20 May - 18 Aug 2025 | 90 | T72 long/flat | 89 (29 / 60) | +5.28% |
| 5 | 21 Nov 2024 - 18 Aug 2025 | 18 Aug - 16 Nov 2025 | 90 | T72 long/flat | 79 (28 / 51) | -2.78% |
| 6 | 19 Feb - 16 Nov 2025 | 16 Nov 2025 - 14 Feb 2026 | 90 | T72 long/short | 116 (12 / 104) | +0.44% |
| 7 | 20 May 2025 - 14 Feb 2026 | 14 Feb - 7 Apr 2026 | 52 | T72 long/flat | 65 (33 / 32) | -0.73% |

In no training window did any configuration stay inside the 8% drawdown
budget, so every pick was "least bad", flagged in `results/folds.csv`.

## 5. Does ARBITER earn its place

The current agents ran in shadow over the research period: 248,544
five-minute samples per asset (27 Nov 2023 to 7 Apr 2026). Each sample was
scored as a hypothetical trade in the score's direction with WARDEN's own
stop, target and time limit, at the measured costs. These overlap heavily;
t-statistics treat each day as one observation.

**1. Does waiting for |score| >= 0.28 improve the outcome?** Barely on ETH,
not at all on BTC, and nowhere near enough.

| | Below 0.28 | 0.28 or more |
|---|---|---|
| BTC, gross per trade | +0.40 bps | +0.10 bps |
| BTC, net | -21.3 | -21.5 |
| ETH, gross | +0.03 | +2.76 |
| ETH, net | -25.7 | -23.0 |

The threshold filters out 65% of setups and the survivors are worth at most
3 bps more, against a 22-26 bps cost.

**2. Does CHARTIST add information beyond QUANT?** Regressing the next
horizon's return on both votes: on BTC neither vote predicts anything
(QUANT -0.7 bps per unit vote, t = -0.4; CHARTIST +1.5, t = 0.6). On ETH
CHARTIST is borderline (+3.9 bps per full-strength vote, t = 1.9) and QUANT
is not (+1.3, t = 0.8). A full-strength CHARTIST vote is worth about 4 bps
over 78 minutes. Agreement between the two: +1.8 bps gross on ETH, zero on BTC.

**3. Does confidence predict returns?** No. Across five confidence bands
QUANT's gross return in its own direction ranges from -0.3 to +0.5 bps on
BTC and -0.7 to +1.3 on ETH, no trend, no band significant. CHARTIST the
same.

**CHARTIST at 95-100% confidence with a vote under 0.10** (the screenshot's
case) occurs on 9% of samples. Its direction was then worth **-0.8 bps on
BTC and -2.5 bps on ETH** before costs. It carries no information.

Full tables: `results/arbiter_study.json`.

## 6. How easily the frozen result breaks

It is a loss, so the question is whether anything turns it into a gain.

| Test | Final test | Research out of sample |
|---|---|---|
| As frozen | -13.29% | -16.65% |
| Slippage doubled | -13.82% | -18.24% |
| Every entry one 5-minute bar late | -11.11% | -18.77% |
| Neighbouring parameters | -21.4%, -24.0%, -33.3% | -18.2%, -28.8%, -52.9% |
| Best five trades removed | -$1,298 (they made $634) | -$1,840 (they made $1,008) |
| Round-trip cost at which it breaks even | **0.6 bps** | **11.4 bps** |
| Actual round-trip cost | 24.2 bps | 24.3 bps |
| Average net per trade, 95% range (weekly blocks) | -23.6 bps (-51.5 to +15.9) | -12.9 bps (-38.7 to +17.5) |

- **By asset (final):** BTC -$193, ETH -$472. Both lose.
- **By month (final):** seven of eight months negative; August +$168.
- **By condition (final):** entries with the 24-hour trend up -16.6 bps,
  trend down -40.3; low volatility -38.8, middle -4.9, high -27.2. No
  condition is profitable. In research, "trend down" and "low volatility"
  were positive; both reversed in the final test.
- **Dependence on a few trades:** five trades made $634 against a total loss
  of $664. Without rare large winners the rule is far worse.
- **Limit fills:** none. Every exit is a market order, so the result does
  not rest on optimistic fills.
- The uncertainty range includes zero. The honest statement is that the
  rule has no demonstrated edge, with a point estimate that loses.

## 7. What removed the apparent edge

Nothing removed it; there was never one net of costs. The decomposition:

| | Gross per trade | Round-trip cost | Net |
|---|---|---|---|
| Short-horizon setups (F1, F3) | about 0 | 24-25 bps | about -25 |
| Volatility breakouts (F2) | +1 to +5 | 26 | -21 to -25 |
| Hour-to-day trend rules (S1, S2) | +11 to +18 in research, **+0.6 in the final test** | 24 | -8 to -24 |

The cost is 10 bps of exchange fee per side, plus half the spread (0.4 bps
BTC, 2.6 bps ETH) and slippage. For any of these to break even the fee
would have to fall by roughly 60-100%.

## 8. Audit trail

| File (under `velocity/shortterm/results/`) | Contents |
|---|---|
| `registry.csv` | All 30 configurations: research-period and out-of-sample results, per asset |
| `folds.csv` | Every fold for every family: dates, pick, trades, return |
| `final_ledger.csv`, `research_oos_ledger.csv` | Every trade of the frozen rule: times, prices, size, costs, exit, balance after |
| `final_skipped.csv` | Every setup not taken, with the reason and what it would have made |
| `final.json` | Final-test results, stress tests, equity curve |
| `arbiter_study.json` | The shadow study |
| `baseline_*.json` | The current agents, with hourly equity curves |
| `equity_final.png`, `equity_research_oos.png` | Equity and drawdown curves |

**Skipped in the final test:** 18 setups, all BTC, all for one reason: one
BTC contract (about $700-840) was more than the shared position limit left
room for while ETH was open. They would have averaged -1.0 bps.

**A winner.** ETH long. Signal known 17 Aug 2026 02:00 UTC, filled at the
02:00 open at $1,889.57. QUANT knew: price 2.45 standard deviations above
its 72-hour average ($1,879.21). CHARTIST: no input (the rule has no
structure condition). SENTINEL: no feed. WARDEN: 5 contracts, $945, cut by
the shared position limit. ARBITER's rule: hourly close above the 72-hour
average, enter long. Exit 23 Aug 05:00 at $2,378.52 when the close fell
below the average (z = -0.16). Held 147 hours. Gross +2,588 bps, costs 26,
funding 11, net +2,550 bps, **+$240.96**.

**A loser.** ETH long. Signal 29 Oct 2026 20:00, filled $2,333.85. QUANT
knew: price 0.01 standard deviations above the average ($2,333.60), the
weakest possible "up". WARDEN: 3 contracts, $700. Exit 21:00 at $2,255.40
when the next hourly close was 3.64 deviations below. Held one hour. Net
-362 bps, **-$25.37**. There is no stop in this rule; the hourly close is
the only exit.

**A WAITING decision.** BTC, 12 Apr 2026 02:00, price $71,676.55. QUANT:
0.86 standard deviations below the 72-hour average ($72,347.97). The rule is
long or flat: ARBITER waits. Rule logged: `WAIT`.

## 9. Code and commands

```bash
pip install -e ".[dev]"

# data: one-minute bars (about 40 minutes per asset) and funding history
python -m velocity.shortterm.data download BTC-USD 2023-11-13 2026-11-13 DATA
python -m velocity.shortterm.data download ETH-USD 2023-11-13 2026-11-13 DATA
python -m velocity.research.funding_data FUNDING
# then convert each CSV once:  velocity.shortterm.data.load(csv) -> np.savez_compressed

# research (only loads data before 8 Apr 2026), freeze, final (runs once)
python -m velocity.shortterm.run research DATA velocity/shortterm/results --funding FUNDING
python -m velocity.shortterm.run freeze   DATA velocity/shortterm/results --funding FUNDING
python -m velocity.shortterm.run final    DATA velocity/shortterm/results --funding FUNDING

# the current agents
python -m velocity.shortterm.existing arbiter  DATA velocity/shortterm/results
python -m velocity.shortterm.existing baseline DATA velocity/shortterm/results research
python -m velocity.shortterm.existing baseline DATA velocity/shortterm/results final

# paper trading (no real orders)
velocity paper
velocity paper --once
```

Configuration: `config/shortterm.json` (risk budget, instrument costs,
`live_orders_enabled: false`) and `config/shortterm_frozen.json` (the frozen
strategy; the paper trader reads this).

### Agent responsibilities in the revised system

| Agent | Code | What it does |
|---|---|---|
| QUANT | `features.py` | Hourly trend score: last completed hourly close minus its T-hour average, in standard deviations. Confidence is that distance; the study found it does not predict returns. |
| CHARTIST | `strategies.py` | Setups from completed bars only, each stamped with the minute it became knowable (`i`), separate from when it began forming (`setup_i`). |
| SENTINEL | `paper.sentinel_status` | Reports `MISSING`, `ACTIVE_QUIET` or `ACTIVE_EVENT`. Trading is price-only and identical in all three. News was not backtested: there is no reliable archive of headline arrival times. |
| WARDEN | `sim.size_position` | Size from stop distance and equity, capped by the position limit and by what the other asset (including its working orders) has used of the shared budget; whole contracts; daily loss limit. |
| ARBITER | `sim.portfolio`, `paper.Trader` | One decision per asset per bar, with the rule logged. Precedence: kill switch, stale feed, stop, target, time limit, rule exit, daily-loss block, enter, wait. |

### Paper trading

- **Same logic as the backtest:** `strategies.setups_for`, `size_position`
  and `TradeState` are shared. A test feeds bars one minute at a time and
  requires the paper trader's trades to equal the backtest's in time, price
  and size, for three families.
- **One owner of orders:** `OrderRouter`.
- **Duplicate signals:** every order has a key, accepted once, kept across
  restarts.
- **Partial fills and timeouts:** a partly filled order keeps what filled
  and cancels the rest after 3 minutes; an unfilled one times out.
- **Stale data:** a bar more than 3 minutes old blocks new entries; more
  than 15 minutes with a position open sends an exit.
- **Restart:** positions, orders, balance and processed signals are saved
  after every bar; a test stops mid-run, restarts and gets identical trades.
- **Kill switch:** a file named `.velocity_kill` flattens and blocks.
- **Live orders:** off, and there is no live adapter in the repository.
- Not supported in paper mode yet: resting stop-entry orders (family F2).

## 10. Forward-test plan

Nothing qualified, so there is nothing to forward-test for money. If a future
candidate passes research and its final test, or if the frozen rule is run
purely to check the plumbing, these are the terms.

| | Requirement |
|---|---|
| Minimum length | 12 weeks **and** 150 closed trades, whichever is later |
| Pass: return | Net return above zero after costs |
| Pass: per trade | Average net above +5 bps, with the 95% weekly-block range not below -5 bps |
| Pass: risk | Drawdown never above 8%; no day worse than -2% plus one trade |
| Pass: consistency | Replaying the backtest over the same dates reproduces at least 95% of the paper trades (time and side), and total P&L within 10% |
| Fail at once | Drawdown above 8%; paper and backtest disagree on more than 5% of trades; any unexplained order |

**Paper fills against the backtest's assumptions: not measured, by
necessity.** The paper broker fills at the next one-minute open using the
same model the backtest uses, so comparing them proves nothing about a real
exchange. Checking the assumed spread (0.73 / 5.16 bps) and slippage (0.5
bps) needs the futures' live bid and ask at each decision. **Missing
dependency:** read-only market-data access to Coinbase's futures book from
the bot (the public API used here serves spot only). Until then these
figures stay blank:

| | Assumed | Observed |
|---|---|---|
| BTC spread at order time | 0.73 bps | |
| ETH spread at order time | 5.16 bps | |
| Slippage beyond the spread | 0.5 bps | |
| Delay from decision to fill | under 60 s | |

## 11. Data and limits

- **Source:** Coinbase Exchange public API, spot BTC-USD and ETH-USD,
  one-minute OHLCV, UTC. 1,578,241 minutes per asset. Missing: 1,001 (BTC)
  and 1,044 (ETH) minutes, 0.06%, the longest 6.5 hours on 8 May 2026 (in the
  final period). Missing minutes are flat bars at the previous close.
- **Spot stands in for the futures.** The futures' own history starts in
  mid-2025. The gap between the two, and the futures' thinner book, are not
  modelled.
- **Measured:** fee tier and one snapshot of each futures spread, 13 Nov
  2026. **Estimated:** slippage. **Proxy:** funding, from Deribit.
- **The final period was not pristine** for two things, as disclosed in the
  plan: the current agents had been replayed on Aug to Nov 2026, and a
  20-bar breakout had been measured over Nov 2024 to Nov 2026. Both lost.
- **Drawdown** is measured on closed-trade equity for the new strategies.
- **The old engine** fills at the deciding bar's close plus a latency
  allowance and applies its daily loss limit per asset.

## 12. Corrections made during the study

Each was a simulator fault, not a change of rule, and each made results
worse or left them unchanged. The frozen strategy's numbers are identical
before and after all three (-13.29%, 309 trades).

1. A rule flipping from long to short had the short refused as "already in a
   position". Found by the gross-versus-cost breakdown. Fixed before the freeze.
2. A market order filled beyond its own stop was treated as not filled,
   avoiding a loss a real order would take. Now a filled, stopped trade.
3. The fix for (1) let a new entry through when the old position's stop
   would be hit in the *next* minute: a one-minute look-ahead. Found by the
   test that compares paper trading with the backtest. Research was rerun;
   the ranking and the winner did not change.
