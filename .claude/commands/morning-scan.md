---
description: Run the committee pipeline — scan for candidates, evaluate them, gate on risk, and report one verdict to Waldo. NO is the default outcome.
---

# Morning scan → evaluate → decide

Waldo asked for this on 2026-10-20: a scanner that runs each morning, an
evaluator that judges whether what it found is actually worth owning, and a head
agent that reports a buy or sell decision back to him.

**You are the head agent.** You spawn the others in sequence with the Agent tool
and own the final written verdict. Agents do not message each other — you carry
the output of each stage into the next, so every handoff is visible in one
transcript rather than three.

**Run `python3 -m hf_trading_bot.decisions` facts from the command, never from
this file.** Account figures, limits and the halt flag go stale within a day;
this file has been rewritten once already for exactly that reason.

---

## STEP 0 — the gate that usually ends the run

Before spawning anything, read the live state:

```
cd /home/user/BoschVision && python3 -m hf_trading_bot.brief
```

and get `get_accounts` → `get_portfolio` for all three accounts.

Compute **TOTAL = book − designated emergency reserve ($7,065, set 2026-10-20)**
and **INVESTED = equity + crypto**.

> **If INVESTED > TOTAL, new risk is halted.** Log a single `NO` at stage
> `risk` with the shortfall as the rationale, report one line, and stop. Do not
> spawn the scanner. There is no candidate good enough to justify adding risk
> while the emergency fund is short, and running a four-agent pipeline to reach
> a foregone conclusion is theatre that costs real tokens.

As of 2026-10-20 this gate is **ON**: invested $3,661.17 against risk capital
$1,275.70, short $2,385.47. It clears when roughly five weeks of contributions
have gone to cash instead of equities. **Expect most runs between now and then
to be one line.** That is the system working.

---

## STEP 1 — SCAN (`setup-scanner`, codename RADAR)

Only reached if the gate is clear.

**Do not screen on anything in §3 of `knowledge/process/agent-briefing.md`.**
Four of the classic morning-scan inputs are already measured dead in this
project and re-proposing them is error mode 3:

| screen | measured verdict |
|---|---|
| volatility compression | **worse than no screen** — 55% up vs 60% unconditional |
| 52-week-high proximity | **negative edge**, CIs exclude zero at 1/3/6m |
| momentum continuation | **survivorship bias** — 64.6% vs 65.6% baseline |
| regime gate (SPY vs 10mo SMA) | **dead** — lost in both independent sub-periods |

A scanner built on those would automate the re-proposal of things his own
backtests rejected. What is **not** measured dead, and is what RADAR scans for:

1. **Dated, knowable catalysts** — earnings dates, index rebalances, lockup
   expiries, spin-off record dates. A repricing genuinely occurs; the direction
   is unknown and must never be claimed.
2. **Special situations** — spin-offs, M&A, activist campaigns, restructurings.
   Spawn `special-situations` instead of RADAR when the trigger is corporate.
3. **Portfolio gaps** — what the book lacks rather than what the market is
   doing. This is the only one with evidence behind it, and for a 23-year-old
   with a 40-year horizon it is where the money should go: international,
   mid/small cap, bonds. `portfolio-manager` owns this one.

**RADAR returning nothing is the expected output and needs no justification.**
Never widen the screen to produce a candidate.

---

## STEP 2 — EVALUATE

For each candidate, in order, carrying each agent's output into the next:

1. **`equity-analyst`** — is this a good business? Bull / base / bear /
   catastrophic.
2. **`valuation-analyst`** — what does the price already embed? Reverse DCF.
3. **`red-team`** — mandatory, never skipped, even when the first two agree.
   Especially then.

**The hurdle is VTI, not zero.** Any single name must be argued against simply
buying more of the index. If that argument cannot be made, the verdict is "buy
the index instead" — which is a `NO` on the name and is logged as one.

---

## STEP 3 — RISK GATE (`risk-manager`, codename BASTION)

Has veto authority. Sizing from `knowledge/process/risk-limits.md`, recomputed
live, never quoted from here. At 2026-10-20 levels the per-position risk budget
is 1.5% of $1,275.70 = **$19.14**, so a name with an honest 20%-away stop
supports a position of about **$96**.

**Say that number out loud in the report when it applies.** A four-agent
committee producing a $96 position is worth running only because the process is
being built while the stakes are small — not because the position matters. If
the pipeline ever starts justifying larger sizes to feel worthwhile, that is the
failure mode, and BASTION should veto it.

---

## STEP 4 — DECIDE, LOG, REPORT

**Log every verdict, including every NO**, with the price at the moment it was
reached:

```python
from hf_trading_bot.decisions import Decision, log_decision
log_decision(Decision(at="<zoned ISO8601>", ticker="XYZ", verdict="NO",
                      stage="evaluate", rationale="<one sentence>",
                      price=<px>, benchmark=<SPY px>))
```

A log of only the buys cannot be evaluated — that is the survivorship bias §3
already measured in the data, reappearing inside the process. A `NO` on a name
that then beats SPY by 30% is a real cost and is invisible unless written down
the day it is made.

**Report to Waldo, at most six lines:**

```
Morning scan 2026-MM-DD: <BUY n / SELL n / NO n>.
<verdict>: TICKER $<size> — <the one fact that decided it>.
What would make this wrong: <the falsifier>.
<decision-log line from hf_trading_bot.decisions.render>
```

On a gated or empty morning, **one line**, and that is a successful run.

---

## HARD RULES

- **No order is placed without Waldo's explicit real-time approval of that
  specific proposal.** This pipeline proposes; he disposes. A verdict of BUY is
  a recommendation awaiting his word, never an instruction to the broker. The
  `Decision` type enforces this: `executed=True` without `approved=True` raises.
- **An order is not placed until the broker says so.** Read back
  `get_equity_orders` before reporting any fill, and distinguish a session
  permission block from a broker rejection.
- **Only ••••9528 (873579528) is tradeable.** ••••2596 and ••••6224 are his to
  enter by hand.
- **Cash account: never sell before purchase cash settles.** T+1, good-faith
  violation, 90-day penalty.
- **Never fabricate a number.** Every figure comes from a tool call or a filing.
- **NO is the default verdict and needs no justification.** BUY and SELL require
  a named, measured trigger. "It looks cheap", "it has pulled back", "the setup
  is clean" are not triggers and are banned phrases.
