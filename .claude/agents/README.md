# The Investment Committee

A team of specialist agents modelled on how an institutional asset manager is
organised, wired to the research skills installed on this account.

## The chart

```
                        CIO  (master orchestrator)
                         │
        ┌────────────────┼────────────────┐
        │                │                │
  portfolio-manager  risk-manager   behavioral-coach     ← the three GATES
        │                │                │
        └────────────────┼────────────────┘
                         │
                   RESEARCH DESK
        ┌──────────┬─────┼──────┬──────────────┐
   equity-    quant-   macro-  special-    setup-
   analyst    analyst  strategist situations scanner
        └──────────┴─────┼──────┴──────────────┘
                         │
                 valuation-analyst
                         │
                     red-team                            ← the MANDATORY gate
                         │
                  DECISION MEMO
```

## Codenames

Each agent has a personal codename used by the Live Agent Cortex dashboard
(`hf-bot dashboard`). The agent `name:` fields are unchanged — the codenames
are a display layer, mapped in `hf_trading_bot/cortex.py`:

| codename | agent | codename | agent |
|---|---|---|---|
| **APEX** | cio | **CIPHER** | quant-analyst |
| **LATTICE** | portfolio-manager | **HORIZON** | macro-strategist |
| **BASTION** | risk-manager | **EMBER** | special-situations |
| **ECHO** | behavioral-coach | **RADAR** | setup-scanner |
| **LEDGER** | equity-analyst | **COMPASS** | valuation-analyst |
| | | **TALON** | red-team |

## How to run it

Full committee review on a name:
```
Use the cio agent to run a full review on ASTS
```

Single specialist:
```
Use the red-team agent to attack my thesis that NVDA is undervalued
Use the risk-manager agent to size a position in AMD for my account
Use the behavioral-coach agent — I just lost money on TSLA and want back in
```

Generate a research queue:
```
Use the setup-scanner agent on the S&P 500
```

## The three hard gates

A BUY cannot be issued unless all three clear:

1. **red-team** — the thesis must survive an adversarial attack, and the
   strongest objection must be answered in writing.
2. **risk-manager** — has veto authority on sizing. Cannot be overridden by
   conviction.
3. **behavioral-coach** — invoked whenever there is urgency, a recent loss,
   a chase, or an unusually large intended size.

These gates exist because the failure mode that destroys retail accounts is
almost never "picked a bad stock." It is unstress-tested conviction, sized
too large, with no exit plan, entered emotionally.

## What this system is and is not

**It is:** a disciplined process that forces written theses, explicit
falsification criteria, adversarial review, and pre-committed exits. It
creates an audit trail so you can grade your own judgement over time and
actually improve.

**It is not:** a source of alpha. No arrangement of language models predicts
short-term price movement. The `setup-scanner` produces a research queue with
honest base rates, never a buy signal. If any agent here starts sounding
certain about where a price is going next, that is a malfunction — treat it
as a bug and distrust the output.

The measurable value of this system is in **errors not made**: positions not
oversized, theses not entered untested, losses not revenge-traded. That is a
real edge, and it is the one actually available to you.

## Non-negotiable rules encoded across every agent

1. Never fabricate a financial figure. `UNKNOWN` beats a plausible invention.
2. Every number carries a source and a period.
3. Facts and assumptions are always visually separated.
4. Every position needs written falsification criteria *before* entry.
5. The benchmark (SPY buy-and-hold) is the hurdle for every active position.
6. `PASS` and `do nothing` are successful outcomes and should be common.
