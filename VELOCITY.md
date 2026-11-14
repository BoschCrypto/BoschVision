# VELOCITY — a multi-agent short-term trading system

Separate from `hf_trading_bot` by design. Different cadence (ticks, not
daily bars), different risk model (intraday, flat by close), different
architecture (five cooperating agents, not one strategy function).

```bash
pip install -e ".[dev]"

velocity bench            # how fast is the hot loop
velocity nulltest         # does it hallucinate edge in noise?  (run this first)
velocity sim              # synthetic market with trend regimes
velocity replay data.csv  # real ticks — the only test that counts
```

---

## 1. The speed problem, stated honestly

You asked for ridiculous speeds and a group of agents that talk to each
other. Those two requirements are in direct conflict, and the conflict has
to be resolved in the architecture rather than wished away:

| | typical latency |
|---|---|
| An LLM agent round trip | 1–20 **seconds** |
| A retail broker order round trip | 20–300 **milliseconds** |
| This system's decision time | ~39 **microseconds** (p50) |
| A colocated HFT firm on a direct feed | sub-microsecond |

If any agent in the decision path is a language model, your bot's reaction
time is measured in seconds and "ridiculous speed" is gone. So VELOCITY
runs **two lanes**:

```
   TICK ──▶ ┌──────────────── FAST LANE (p50 39us, no I/O, no model calls) ─────────┐
            │                                                                        │
            │   QUANT ──┐                                                            │
            │           ├──▶ ARBITER ──▶ WARDEN (hard veto) ──▶ order                │
            │ CHARTIST ─┘         ▲                                                  │
            │                     │ reads a float                                    │
            └─────────────────────┼──────────────────────────────────────────────────┘
                                  │
            ┌─────────────────────┴──── SLOW LANE (own thread, seconds) ─────────────┐
            │   SENTINEL — polls news, scores it, writes one decaying number         │
            └────────────────────────────────────────────────────────────────────────┘
```

SENTINEL never blocks a trade. It writes a bias scalar to the shared
blackboard; the fast lane reads it in nanoseconds. If the news API hangs,
times out or returns garbage, the signal ages out and the system trades on
price alone. That is the correct degradation, and it is tested
(`test_sentinel_survives_a_broken_feed`).

**What the speed actually buys you.** Not a latency race — you will lose
that to firms with microwave links and exchange colocation, permanently.
What it buys is that *compute is never your bottleneck*: the full five-agent
swarm re-evaluates on **every single tick** instead of sampling every few
seconds, so a stop is never late and a reversal is never missed because the
bot was busy. Your real bottleneck is your broker's 20–300 ms, and no code
of mine changes that.

---

## 2. The five agents

| Agent | Lane | Job | Can it open a trade? |
|---|---|---|---|
| **SENTINEL** | slow | News & event scanning | No — votes small, **vetoes hard** |
| **QUANT** | fast | Technical analysis | No — votes |
| **CHARTIST** | fast | Chart structure & microstructure | No — votes |
| **WARDEN** | fast | Risk, sizing | No — **absolute veto** |
| **ARBITER** | fast | Overseer: LONG / SHORT / FLAT | **Yes** — the only one |

### They genuinely interact

This is not five indicators averaged together. Each reads the others off the
blackboard and changes its own behaviour:

- **QUANT reads SENTINEL.** When a headline is live it cuts the weight on its
  own mean-reversion term, because news-driven moves *continue* far more
  often than they revert. Fading a move that had a reason is the most
  expensive habit a mean-reversion scalper has.
- **CHARTIST reads QUANT.** A level break with the trend is continuation at
  full weight; a break against it is suspect and gets halved.
- **CHARTIST hunts failed breaks.** A break of the high on *falling*
  participation into an already-stretched range is scored as a **trap** and
  voted the *opposite* way — that is where trapped buyers are.
- **ARBITER requires confluence.** No single agent, at any confidence, can
  open a position. At least two must independently agree
  (`test_arbiter_requires_confluence`).
- **Disagreement shrinks size, it is not averaged away.** Two agents pulling
  opposite ways is *information* — it says the setup is unclear — and a
  plain average destroys exactly that information.
- **WARDEN cannot be outvoted.** Three unanimous agents still lose to the
  risk gate (`test_warden_veto_cannot_be_outvoted`).

---

## 3. WARDEN's limits

Everything else is an opinion. WARDEN is a constraint.

| Limit | Default | Why |
|---|---|---|
| Kill switch | `.velocity_kill` file | Stop it from your phone, not a terminal |
| Daily loss | 2% of equity | **Terminal for the day.** Letting a bot "make it back" is how a bad day ends an account |
| Drawdown brake | 1.5% peak-to-trough | Catches a slow bleed before the daily stop |
| Risk per trade | 0.4% of equity to the stop | Volatility-targeted, not fixed share count |
| Max position | 25% of equity | |
| Loss streak | 3 in a row → cooldown | A model wrong 3× running is more likely mis-specified than unlucky |
| Re-entry cooldown | 20s | Kills the stop-out-then-immediately-re-enter loop |
| Trade rate | 6/min, 250/session | Circuit breaker for a signal that starts oscillating |
| Volatility spike | 3× normal ATR → stand down | The stop you sized on no longer exists |
| Spread guard | 8 bps | Negative expectancy before you start |
| Session cutoff | flat by close | Overnight gap is not a risk an intraday system is paid for |

---

## 4. Measured results, and how to read them

### Latency (`velocity bench`, 300k ticks)

```
p50  38.90 us     p90  49.15 us     p99  83.49 us     p99.9  162.30 us
22,085 ticks/sec end-to-end, full five-agent pipeline
```

### The null test (`velocity nulltest`) — **run this first, always**

Same swarm, driftless random walk, no exploitable structure exists:

```
 seed  trades     win    pre-cost         net      ret
   11     250  35.2%  $     -654  $    -1,199   -1.20%
   23     250  36.0%  $     -658  $    -1,151   -1.15%
   77     250  38.4%  $   -1,002  $    -1,337   -1.34%
  101     250  38.8%  $     -987  $    -1,330   -1.33%
  202     201  32.8%  $     -812  $    -1,169   -1.17%
```

Pre-cost P&L is ~zero and net is ~minus the costs. **This is the correct
result.** The swarm does not manufacture signal out of noise. If this test
ever comes back *positive*, you have a look-ahead leak or an accounting bug,
not a discovery — it is asserted in CI (`test_null_test_finds_no_edge_in_noise`).

### The synthetic market (`velocity sim`) — **do not believe this number**

```
seed  11: trades=250 win=63.6% net=$36,078  PF=6.50
seed  23: trades=250 win=77.6% net=$53,062  PF=10.08
```

+36% to +53% — and it is **meaningless**. I generated that market with
built-in multi-thousand-tick trend regimes, so a trend-following swarm
harvests structure I planted myself. Measuring edge against it is circular.

The honest reading of these two tests together is the genuinely useful
output: **VELOCITY is a trend/continuation harvester with a hard risk
frame.** It prints money when sustained directional moves exist and loses
its trading costs when they don't. The only open question is whether real
intraday data contains enough of that structure *net of costs* — and only
`velocity replay` on real, out-of-sample data answers it.

---

## 5. Two bugs found during the build, kept here because they're instructive

**1. Stops sized per-tick.** ATR on a 100ms tick stream measures one tick's
move. A 2×ATR stop sat two ticks away, so ordinary noise took it out
instantly — average hold was **0.6 seconds** and no trade ever reached its
thesis. Fix: dispersion grows with √time, so a trade held N ticks needs a
stop in units of `ATR × √N`. Average hold went 0.6s → ~19s.
(`test_stop_scales_with_holding_horizon`)

**2. Exits on single-tick noise.** One tick dipping below the exit band
closed positions. Fix: the exit condition must persist 5 consecutive ticks
*and* clear a minimum hold — except a hard flip, which bypasses both,
because being wrong-way in size beats paying a spread.

Both are the kind of bug that makes a backtest look merely mediocre rather
than broken, which is why they survive in a lot of trading code.

---

## 6. Before this touches real money

```
1. velocity nulltest        confirm it finds nothing in noise
2. velocity replay <csv>    real out-of-sample ticks, 100+ trades
3. paper trade live         real feed, weeks, journaled
4. only then, size small    kill switch reachable from your phone
```

`velocity live` **refuses to run** and prints that list. There is no live
broker adapter wired in, deliberately. Steps 1–3 cost nothing but time.

Two things worth saying plainly given what this is for:

- **Costs dominate at this frequency.** In the null test the swarm was
  roughly flat before costs and lost 1.2% after them. At ~19 second holds
  you cross the spread constantly, and the spread is the house edge. Any
  real edge has to clear that bar before anything else is true.
- **Short-term trading is where retail edge is weakest**, competing directly
  against the best-capitalised, lowest-latency participants in the market.
  Your `ROADMAP.md` already makes this case at length and the arithmetic
  there hasn't changed. This system is built properly and it is honest with
  you about what it finds — but build it, measure it, and size it as the
  high-variance sleeve it is, not as the plan.

---

## 7. Layout

```
velocity/
  types.py          Tick, Signal, Decision, Verdict — __slots__, no allocation
  bus.py            Blackboard: lock-free last-value-wins shared state
  indicators.py     streaming O(1) EMA/RSI/ATR/VWAP/z-score/rolling extrema
  agents/
    quant.py        technical analysis
    chartist.py     structure, breaks, trap detection
    sentinel.py     news (slow lane), lexicon + optional LLM escalation
    warden.py       risk gate, volatility-targeted sizing
    arbiter.py      fusion, confluence, veto handling, the decision
  execution.py      pessimistic fill model: spread + slippage + latency drift
  engine.py         hot loop, slow-lane supervisor, latency recorder
  feeds/            synthetic (with a drift_scale=0 null mode), CSV replay,
                    coinbase_history (1-minute candle download)
  presets.py        perp_engine: the Coinbase perpetual-futures configuration
  recorder.py       records what every agent said during a run (dashboard data)
  mind.py           live view: frame builder, local web server, live and replay sources
  web/              the page it serves (mind.js draws the spheres)
  cli.py            sim / nulltest / bench / replay / fetch / live
tests/test_velocity.py   51 tests
```

---

## 8. Crypto perpetual futures: tested on real data, and it loses

**Verdict: as built, VELOCITY has no measurable edge on BTC or ETH at
Coinbase's fees. Do not trade it.** Gross P&L is about zero; fees are the
whole loss.

### Venue facts (from the Coinbase API, 13 Nov 2026)

| | |
|---|---|
| Tradeable from the US | `FUTURE` products on Coinbase Derivatives Exchange, with futures approval |
| Perpetual-style contracts | `BIP-20DEC30-CDE` (0.01 BTC), `ETP-20DEC30-CDE` (0.1 ETH): hourly funding, expiry decades out |
| Not available | `GLOBAL_PERP` (Coinbase International) |
| Futures fees, Intro tier | 9.5 bps maker / 10 bps taker per side: **20 bps round trip** |
| Spread | about 0.01 bps on BTC, so the fee is the cost, not the spread |
| Spot fees, Intro tier | 50 / 90 bps: unusable for short-term trading |

### Why the holding period had to change

Measured 1-minute volatility is 5.0 bps (BTC) and 6.8 bps (ETH). A 19-second
hold moves about 3 bps against a 20 bps cost. For the cost to be a third of a
one-sigma move the hold must be about **144 minutes on BTC and 78 on ETH**
(`presets.horizon_for`). The `perp` preset runs on 1-minute bars at those
horizons; it is an intraday trend system, not a scalper.

### Results: $5,000 account, 10 bps per side, 1-minute bars

> These are the numbers from **before** the fixes in section 11. The bot was
> losing less here partly because a bug kept sidelining it. Section 11 has
> the current numbers.

Signal parameters were fixed before any P&L was seen. The first window was
split 30 days / 15 days; the holdout is the 45 days before it and was
downloaded only after the first window had been analysed.

| Data | Market | Trades | Before costs | Net | t-stat |
|---|---|---|---|---|---|
| BTC, first 30 days | falling | 34 | -$26 | **-$88** (-1.75%) | -4.9 |
| BTC, last 15 days | falling | 24 | -$27 | **-$67** (-1.34%) | n < 30 |
| ETH, first 30 days | falling | 141 | +$0 | **-$114** (-2.28%) | -3.3 |
| ETH, last 15 days | falling | 80 | -$31 | **-$94** (-1.89%) | -4.0 |
| BTC, holdout 45 days | +31.6% | 47 | +$21 | **-$55** (-1.11%) | -1.7 |
| ETH, holdout 45 days | +41.3% | 202 | +$8 | **-$159** (-3.19%) | -2.8 |
| **Total** | | **528** | **-$56** | **-$578** | |

Six runs, six losses. Fees were $522 of the $578.

Against buy-and-hold: in the rising holdout the strategy trailed by 33 and 44
points. In the falling window it "beat" buy-and-hold by about 11 points only
because it was mostly flat while the market fell; it still lost money.

The null test at this configuration (driftless random walk, same costs) lost
0.2% on BTC and 3.7% on ETH, so the harness is not inventing edge.

### One hypothesis tested, and rejected

In the first window, trades that reached a stop or target looked good (43
targets to 33 stops) and the loss came from the signal-decay exit: 179 trades,
20% in the right direction. Disabling that exit (`--no-decay-exit`) was tested
on the untouched holdout:

| | BTC holdout | ETH holdout | BTC first window | ETH first window |
|---|---|---|---|---|
| Net, no decay exit | +$52 (t = +0.7) | -$92 | -$125 | -$55 |

Three of four lose and the one gain is indistinguishable from zero. Win rates
came out at 36-44%, which is what a random walk gives for a 3:2 target-to-stop
ratio. The apparent 57% was an artefact of the decay exit removing trades
before they resolved.

### Three defects the real data exposed

All three were invisible on the synthetic series, which starts at price 100.

1. **WARDEN refused every trade** on a $73,000 instrument: its minimum-size
   check was `notional < price`. Now rounds to whole contracts
   (`RiskLimits.contract_size`).
2. **ARBITER had the same check** after its dispersion haircut and blocked
   7,081 entries on ETH. Now rounds through WARDEN; when the haircut leaves
   less than one contract, the trade is taken only at half conviction or better.
3. **The daily loss limit never reset** on a multi-day run. The blackboard now
   rolls the day on the UTC date.

Also added: buy-and-hold benchmark, time in market, a t-statistic on per-trade
P&L, and veto counts grouped by reason.

### Limits of this test

- Price series is Coinbase **spot** BTC-USD / ETH-USD, used as a proxy for the
  futures. Basis and the futures' own liquidity are not modelled.
- 90 days, two regimes, one fee tier. Funding is not charged (about 0.05 bps
  an hour at the time of writing).
- One-minute bars: fills inside a bar are not modelled.
- BTC is at the edge of what a $5,000 account can size: one BIP contract was
  refused about 11,600 times in the holdout for being larger than the risk
  budget allowed. ETH's $200 contract fits.

### Reproduce

```bash
velocity fetch BTC-USD --days 45 --out btc.csv
velocity replay btc.csv --preset perp --contract-size 0.01 --horizon 144
velocity fetch ETH-USD --days 45 --out eth.csv
velocity replay eth.csv --preset perp --contract-size 0.1 --horizon 78
```

`--end <epoch seconds>` on `fetch` pulls an earlier window to use as a holdout.

---

## 9. Seeing the agents interact

```bash
velocity replay eth.csv --preset perp --contract-size 0.1 --horizon 78 --record rec/
```

`--record` writes five JSON files describing the run: each agent's vote and
the arbiter's score over time, every trade with the votes that opened it,
what happened on every bar, how often QUANT and CHARTIST agreed (and whether
the market then moved their way), and counts of each agent-to-agent handoff.
The recorder only observes; a recorded run is identical to an unrecorded one
(`test_recorder_captures_a_run_without_changing_it`).

SENTINEL shows zero activity in any replay: it needs a live news source.

---

## 10. The live mind view

```bash
velocity mind                      # ETH, live Coinbase prices, paper trading
velocity mind --symbol BTC-USD --contract-size 0.01 --horizon 144
velocity mind --replay eth.csv --speed 20
```

Then open http://127.0.0.1:8765. Each agent is a sphere of orbiting light:
how fast it turns and how bright it burns is the strength of its current
view, and beams between spheres carry what one agent is telling another
(QUANT's trend to CHARTIST, both votes to ARBITER, proposals to WARDEN and
its refusals back). Readouts under the stage give the same state as text,
and a log lists each handoff as it happens.

How live it is: the price updates every two seconds; the agents form a new
view once a minute, when each one-minute bar closes, because that is the
cadence the tested configuration runs at. On start it warms the indicators
on the last 30 hours of candles **without trading**, so position and P&L
count only from the moment you opened it.

It is paper trading only. No order leaves the process. Closed trades are
appended to `velocity-mind-trades.csv`. On Windows, double-click
`run-velocity-mind.bat`.

SENTINEL reads two public crypto news feeds (Cointelegraph and CoinDesk) once
a minute. It shows LISTENING while nothing recent concerns the market being
traded, and votes only on headlines that name the asset (full weight) or
Bitcoin and the crypto market generally (half weight). Scoring is by keyword,
which is crude, and public feeds run minutes behind the wires; treat it as
context and a veto, not a signal. `--no-news` turns it off.

To flatten and stop trading, create a file named `.velocity_kill` in the
folder you started it from; delete the file to resume.

---

## 11. Fixes of 13 November 2026, and the results after them

An audit of whether the thing actually works found these. Each has a test.

| Defect | What happened | Fix |
|---|---|---|
| Kill switch did not flatten | Risk checks ran only when *entering* a trade. A kill file created with a position open left it open 54 bars, until its own stop. The daily loss limit had the same hole. | WARDEN's hard stops now run on every bar that has a position open (`must_flatten`). Confirmed on the live bot: position closed on the next bar. |
| Kill switch checked every 500 ticks | Over eight hours on one-minute bars. | Checked once a second of market time. |
| VWAP never reset | On a 24-hour market CHARTIST's "distance from VWAP" drifted past 100 ATRs and pinned its vote one way. "Both agents say short" was 2% of bars in a run where it should have been far more. | Resets each UTC day. Now 16% of bars. |
| Loss streak cleared only on a win | WARDEN shrinks size in a streak; on a small account the shrunken size is under one contract, so no trade, so no win, so the streak never cleared. | A new UTC day clears it. |
| Live mode dropped bars | After an outage it asked for the last 5 bars, whatever the gap. | Asks for every bar since the last one fed. |
| Old headlines read as breaking news | SENTINEL dated a headline by when it first read it. | Dated by publication time; anything older than six half-lives is ignored. |
| SENTINEL had no source | One of five agents never spoke. | Reads RSS/Atom; two public feeds wired into `velocity mind`. |
| No record of paper trades | The book lived in memory only. | Closed trades append to a CSV. |

### Results after the fixes

Same data, same costs, same signal settings as section 8.

| Data | Market | Trades | Before costs | Net | t-stat |
|---|---|---|---|---|---|
| BTC, first 30 days | falling | 115 | -$51 | **-$262** (-5.24%) | -6.3 |
| BTC, last 15 days | falling | 59 | -$26 | **-$126** (-2.52%) | -4.5 |
| BTC, holdout 45 days | +31.6% | 167 | +$3 | **-$283** (-5.66%) | -4.7 |
| ETH, first 30 days | falling | 186 | +$12 | **-$150** (-3.01%) | -4.9 |
| ETH, last 15 days | falling | 95 | -$39 | **-$119** (-2.38%) | -4.5 |
| ETH, holdout 45 days | +41.3% | 270 | -$21 | **-$247** (-4.94%) | -4.4 |
| **Total** | | **892** | **-$122** | **-$1,188** | |

The bot now trades about 70% more often, because it no longer gets stuck
after three losses, and it loses about twice as much: **-$1,188 against
-$578**. Before costs it is still roughly flat (-$0.14 a trade). Fees of
$1,065 are the loss.

So the fixes made the machine work as designed, and working as designed it
is a more reliable loser. The earlier, smaller loss was the deadlock saving
fees by accident. Nothing here changes the verdict in section 8; it
strengthens it. No run triggered the daily loss limit with a position open.

The null test (driftless random walk, same costs) lost 6.3% on BTC and 5.5%
on ETH: no edge invented from noise.

SENTINEL's news has **not** been tested on history, because there is no
archive of timestamped headlines to replay. With it on, live behaviour can
differ from every table in this document.

---

## 12. Short-term strategy study (5 minutes to 1 hour)

A full study of short-term BTC and ETH strategies on Coinbase futures: 30
configurations in five families, walk-forward selection, one final test.
**Nothing qualified.** The least-bad rule lost 13.3% on its final test; the
current five agents lost 7.9% before their own drawdown rule stopped them.

- Plan, fixed first: `velocity/shortterm/PLAN.md`
- Report: `velocity/shortterm/REPORT.md`
- Paper trading with the backtest's own logic: `velocity paper`

---

## 13. Agent teamwork, tested properly (velocity/teamwork/)

The five-agent system failed partly because its agents all read price, so
their agreement added nothing. This study gave six agents six genuinely
different inputs (price, volume, funding, implied volatility, VIX, market
breadth) and combined them by conjunction rather than averaging.

Measured independence: average correlation between agents 0.19. The design
worked. The result did not: **0 of 290 conditions cleared the bar**, and
the largest t-statistic anywhere was 2.10 against the 3.08 that pure noise
would have produced across that many tests.

- Plan, fixed first: `velocity/teamwork/PLAN.md`
- Report: `velocity/teamwork/REPORT.md`
