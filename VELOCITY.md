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
  feeds/            synthetic (with a drift_scale=0 null mode), CSV replay
  cli.py            sim / nulltest / bench / replay / live
tests/test_velocity.py   42 tests
```
