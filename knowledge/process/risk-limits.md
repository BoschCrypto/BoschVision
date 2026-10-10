# Risk limits — rebuilt 2026-09-29 for a contributing account

## Why the old limits are void

They were fixed dollars sized to a $2,197 static account: a $439 "satellite reserve", a
$39.54 (1.8%) risk budget, max 3 open names, 20%/12%/35% caps. At **$2,000/month of new
contributions** the account passes $27,000 within a year, so every dollar figure is stale
within weeks of being written. Fixed-dollar limits on a growing account are not limits.

**Rule: all limits are percentages of the current total, recomputed at each review.
No dollar figure is ever hardcoded again.**

## The limits

| limit | value | why |
|---|---|---|
| Risk budget per position | **1.5% of total** | Position size = budget ÷ distance-to-stop. A name whose honest stop is 25% away cannot be a 6% position. |
| Max single non-diversified name | **10% of total** | |
| Max single-name look-through | **12% of total** | Must count **every** fund that holds it — see below. |
| Max sector look-through | **35% of total** | |
| Max open single-name positions | **5** | Raised from 3: the account can now carry them. |
| Halt new risk | portfolio **−15%** from high-water mark | |
| Emergency fund | **6 × fixed monthly expenses**, in savings | Never invested. Not part of the risk calculation. |
| Core allocation | **≥70%** broad index | The satellite is what is left, not a fixed $439. |

## Look-through is computed across ALL funds, always

On 2026-09-28 I reported NVDA look-through as 4.5%, having counted direct NVDA and NVDA
inside VGT while **omitting NVDA inside VTI** — the largest holding. The true figure was
6.72%. Nothing followed from the error, but **a cap computed on a subset of the funds
holding a name is not a cap.**

    look-through(X) = direct X + Σ over every fund held (fund value × X's weight in it)

Every fund weight must be **verified from the issuer's holdings page**, never assumed. If a
weight cannot be verified, state the conclusion across a plausible range and say the weight
is unverified.

## Account-type rules

- **Momentum / any high-turnover strategy: tax-advantaged accounts only.** Measured
  2026-09-28: 18.6%/yr and Sharpe 1.07 untaxed, versus 12.9% and 0.81 taxed, where the
  taxed version lost to SPY in the first half of the sample on both return and Sharpe.
- **Taxable account: buy and hold broad index funds.** Deferred tax is the edge.
- **Cash account: never sell before purchase cash settles** (good-faith violation, 90-day
  penalty). T+1.

## Standing rule, unchanged by any of the above

**No order is placed without the principal's explicit real-time approval of that specific
proposal.** A general "do as you see fit" delegates judgement, never execution. This does
not loosen when he is asleep, when a number looks urgent, or when an agent is confident.
