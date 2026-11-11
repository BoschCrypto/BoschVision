# Risk limits — rebuilt 2026-09-29 for a contributing account

## Why the old limits are void

They were fixed dollars sized to a $2,197 static account: a $439 "satellite reserve", a
$39.54 (1.8%) risk budget, max 3 open names, 20%/12%/35% caps. At **$2,000/month of new
contributions** the account passes $27,000 within a year, so every dollar figure is stale
within weeks of being written. Fixed-dollar limits on a growing account are not limits.

**Rule: all limits are percentages of the current total, recomputed at each review.
No dollar figure is ever hardcoded again.**

**Rule, added 2026-10-20: every limit names its base in the table.** Three bases
exist — TOTAL, INVESTED, COMMITTED — and they are not interchangeable; cash is
56% of the book, so the base decides whether half his money counts as armour or
as nothing. The ruling and the reasoning that produced it are in
`denominators.md`. A percentage printed without its base is a bug, not a
shorthand: the routines raise rather than print one.

## The limits

| limit | value | base | why |
|---|---|---|---|
| Risk budget per position | **1.5%** | TOTAL | Position size = budget ÷ distance-to-stop. A name whose honest stop is 25% away cannot be a 6% position. |
| Max single non-diversified name | **10%** | TOTAL | |
| Max single-name look-through | **12%** | TOTAL | Must count **every** fund that holds it — see below. |
| Max sector look-through | **35%** | TOTAL | |
| Max third-party-directed mandate | **10%** | TOTAL | Added 2026-10-20. The *mandate* is the exposure, not tonight's position: when someone else sets the size, the whole committed amount is live. Currently 15.48% — **over**. |
| Max open single-name positions | **5** | — | Raised from 3: the account can now carry them. |
| Halt new risk | **−15%** from high-water mark | TOTAL | |
| Emergency fund | **$7,065** designated (3 × $2,355) | — | Never invested. Carved out of TOTAL before any cap is computed. Designated by Waldo 2026-10-20; **6 months ($14,130) is the destination, not today's line.** Only $4,679.53 of it exists in cash — see the precedence rule below. |
| Core allocation | **≥70%** broad index | **INVESTED** | The one limit that is not on TOTAL: it is a mix limit, and on TOTAL it could only ever be satisfied by spending the emergency fund on equities. QQQ does not count as broad. |
| Strategy return / drag / realised P&L | — | **COMMITTED** | Frozen at the moment capital was assigned. Every other base flatters the result by growing the denominator. |

## Precedence: when INVESTED exceeds TOTAL, there is one breach, not six

Added 2026-10-20, the moment the reserve was designated, because the designation
immediately produced this state and the limits as written would have reported it
six times.

With a $7,065 reserve carved out of an $8,340.70 book, **TOTAL (risk capital) is
$1,275.70 while INVESTED is $3,661.17** — 287% of it. Every per-name cap is
then mechanically breached by the same scalar:

| limit | cap on $1,275.70 | current | |
|---|---|---|---|
| third-party mandate, 10% | $127.57 | $1,290.84 | 101% of risk capital |
| sector look-through, 35% | $446.50 | $1,784–$2,001 | 140%–157% |
| single-name look-through, 12% | $153.08 | $379–$439 | 30%–34% |
| single non-diversified, 10% | $127.57 | $0 crypto deployed | the only one clear |

**These are not five findings. They are one fact seen five times**, and the
arithmetic says so exactly:

    reserve − cash  =  $7,065.00 − $4,679.53  =  $2,385.47
    invested − TOTAL = $3,661.17 − $1,275.70  =  $2,385.47

Identical by construction, since book = cash + invested = reserve + TOTAL. The
single fact underneath: **$2,385.47 of the money designated as the emergency
fund is not in cash — it is in VTI, VOO, QQQ, VGT and NVDA.** That is precisely
the arrangement the emergency-fund rule exists to forbid, because it means the
fund can only be drawn by selling equities in the scenario the fund exists for.

**The rule:** while INVESTED > TOTAL, the routines report the aggregate shortfall
and **suppress the per-name caps**, which carry no information in this state —
they are all the same number wearing different labels. Reporting six breaches
where there is one is the same failure as printing a percentage without a base.
Per-name caps resume the day INVESTED ≤ TOTAL.

**The cure requires no sale.** Route the next **$2,385.47** of contributions to
cash rather than equities; at $2,000/month that is about five weeks. Until then,
**halt new risk** — which is the existing limit doing its job, not a new one.

**Consequence for the signal sleeve, effective before the next non-cash post:**
a compliant mandate is 10% of $1,275.70 = **$127.57**, against $1,290.84
committed. The sleeve is in cash tonight so nothing needs selling, and the next
non-cash signal will be proposed at **$127.57, not $1,290.84**, unless Waldo
says otherwise at that moment — as every order requires anyway. Noted without
being used as an argument: this arrives at almost the same place as H16's cost
analysis by an entirely independent route, and two unrelated lines of reasoning
landing together is worth more than either alone.

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
