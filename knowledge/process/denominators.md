# Denominators — which base each limit uses, ruled once

Written 2026-10-20 because this question has been open for a week, now carries
real money, and the routines have been hedging it every morning by reporting two
numbers and letting whoever reads them pick. Two numbers for one limit is not
caution; it is an unmade decision wearing caution's clothes.

Everything below is computed from live broker reads taken 2026-10-19 22:16 ET,
not from any file in this repo. That distinction turned out to matter — see
§6.

---

## 1. There are exactly three bases, and they are not interchangeable

| base | definition | value now |
|---|---|---|
| **TOTAL** | every account's `total_value`, summed, **less any dollars formally designated as the emergency reserve** | **$8,340.70** (no reserve designated — see §5) |
| **INVESTED** | equity + crypto, marked to market. Cash excluded. | **$3,661.17** |
| **COMMITTED** | dollars assigned to a strategy sleeve, frozen when assigned, never revalued | **$1,290.84** (the signal sleeve) |

The live book:

| account | type | equity | cash | crypto | total |
|---|---|---|---|---|---|
| ••••2596 | cash, default | $2,764.74 | $3,490.05 | $0.00 | $6,254.79 |
| ••••9528 | cash, "MCP" | $896.43 | $1,189.24 | $0.00 | $2,085.67 |
| ••••6224 | limited margin, **managed** | $0.00 | $0.24 | — | $0.24 |
| | | **$3,661.17** | **$4,679.53** | **$0.00** | **$8,340.70** |

Cash is **56.1% of the book**. That is the entire reason the base matters: a
limit's denominator decides whether more than half his money counts as armour or
as nothing.

Marks: VTI 375.54, VOO 704.60, QQQ 718.36, VGT 120.33, NVDA 217.09 — official
2026-10-19 closes, not after-hours prints, so the figures reproduce.

---

## 2. The ruling

> **Concentration caps use TOTAL. The core index floor uses INVESTED.
> Strategy performance uses COMMITTED.**

### Concentration caps → TOTAL

The four caps — single non-diversified name (10%), single-name look-through
(12%), sector look-through (35%), and the crypto mandate — are **loss-bound**
limits. The numerator is capital exposed to one failure, and the question they
answer is *how much of my money dies if this one thing dies.* Cash cannot die.
It belongs in the denominator.

### Core index floor → INVESTED

The ≥70% broad-index floor is a **mix** limit. The numerator is capital in the
core strategy, and the question is *of the money I have put at risk, how much is
in the thing I actually believe in.* Cash has been allocated to neither core nor
satellite, so it belongs in neither numerator nor denominator.

### Strategy performance → COMMITTED

Returns, drag, and realised P&L are measured against the money handed to the
strategy, frozen at the moment it was handed over. This base is not negotiable
on the same grounds as the other two; it exists because every other choice
flatters the result.

---

## 3. The test that produces the split

One question settles both halves: **which way does holding cash push the number,
and is that the right signal?**

| | effect of holding more cash | correct? |
|---|---|---|
| caps on TOTAL | concentration falls | **yes** — he genuinely is less exposed |
| floor on INVESTED | no effect | **yes** — holding cash is not a diversification failure |
| floor on TOTAL | floor "breached" | **no** — and it is the dangerous one |
| caps on INVESTED | concentration rises | **no** — de-risking reads as a breach |

The last two rows are not symmetric annoyances; they are both failures of the
same kind, and each is disqualifying on its own.

**A floor on TOTAL can only be satisfied by buying.** Broad index is 27.8% of
total against a 70% floor. Curing that requires deploying **$11,728** — against
$4,679.53 of cash which is 1.99 months of living expenses. A risk limit whose
only remedy is to spend the emergency fund on equities is not a risk limit; it is
a buy signal with a compliance font. It has been reading BREACH every morning
since the Roth was opened, purely because the money has not been wired yet.

**Caps on INVESTED punish de-risking.** Tonight's signal going to 100% cash is
an unambiguous reduction in risk. On TOTAL, tech look-through barely moves. On
INVESTED, removing the crypto shrinks the denominator and tech look-through
*rises* — the book reports that de-risking made the breach worse. A denominator
that reacts perversely to de-risking is the wrong denominator.

**The two halves also close each other's holes, which is why this is a ruling
and not a compromise.** Caps on TOTAL alone could be gamed: park cash, make room
for a bigger speculative bet. The floor on INVESTED independently blocks that,
because the satellite can never exceed 30% of risk capital no matter how much
cash sits beside it. A floor on INVESTED alone would ignore cash's real
loss-absorption, which the caps on TOTAL capture. The holes point in opposite
directions.

---

## 4. What the ruling says about the book tonight

| limit | base | reading | status |
|---|---|---|---|
| single non-diversified name, 10% | TOTAL | crypto deployed **$0.00** | **no breach** |
| crypto *mandate*, 10% | TOTAL | **$1,290.84 = 15.48%** | **over by $456.77** |
| NVDA look-through, 12% | TOTAL | **4.5%–5.3%** | OK, ~2.3× headroom |
| tech look-through, 35% | TOTAL | **21.4%–24.0%** | OK, ~1.5× headroom |
| broad-index floor, ≥70% | INVESTED | **63.4%** (VTI + VOO = $2,320.19) | **short** |

Four notes, in descending order of how much they should bother him:

**The mandate is the breach, not the position.** The sleeve holds nothing
tonight, so there is no position to trim. But a third party who has now gone
0→100%→0 twice in ten days decides the size, and the whole $1,290.84 is
therefore live exposure regardless of what is deployed on any given evening.
Per-coin caps are meaningless here — intra-crypto correlation runs 0.38 to 0.85,
so four coins are one bet with extra spread. Measured as one non-diversified
bet it is 15.48% against a 10% cap. **The cheapest moment in months to shrink a
mandate is while it sits in cash, which is right now.** That is an observation,
not an order: nothing goes in without his explicit approval of the specific
order.

**The floor is short and the cure is addition, not a sale.** $808.76 into VTI or
VOO brings it to exactly 70.0% of invested. Any Roth funding into a broad index
cures it outright. **Nothing in this document obliges selling anything.**

**QQQ is the numerator ambiguity, and it is not "broad."** Counting QQQ as broad
index would read 77.5% and clear the floor. Nasdaq-100 is 100 names and roughly
60% technology; it is a sector bet with an index ticker. It stays in the
satellite. Writing this down now, while it is inconvenient, is the point of
writing it down.

**Every look-through figure is still an estimate.** No issuer holdings page has
ever been confirmed from this environment — Vanguard and Invesco are both
proxy-blocked. The bands above span plausible weights, and the caps clear at the
top of every band, so estimation error does not change the verdict. But note
what the base choice does here: on INVESTED, NVDA look-through reads 10.3%–12.0%
against a 12% cap, so the limit would be decided at the fourth significant
figure of a number nobody has verified. On TOTAL it clears with 2.3× headroom.
**A base that makes unverifiable inputs load-bearing is the wrong base**, and
that is a third independent argument for TOTAL that has nothing to do with the
first two.

---

## 5. Settled 2026-10-20: the reserve is $7,065

When this document was written the reserve was undesignated, so TOTAL had no
definition and every cap was an opinion with a number attached. Waldo ruled the
same night and picked the uncomfortable option:

> **Emergency reserve = $7,065 (3 × $2,355), carved out of TOTAL. Six months
> ($14,130) is the destination, not today's line.**

**TOTAL is therefore $1,275.70**, and the book reads very differently from §4:

| limit | cap | current | |
|---|---|---|---|
| third-party mandate, 10% | $127.57 | $1,290.84 | **101% of risk capital** |
| sector look-through (tech), 35% | $446.50 | $1,784–$2,001 | **140%–157%** |
| single-name look-through (NVDA), 12% | $153.08 | $379–$439 | **30%–34%** |
| single non-diversified, 10% | $127.57 | $0 deployed | clear |
| broad-index floor, ≥70% of INVESTED | — | 63.4% | short $808.76 |

The §4 table is **not** retracted; it was correct for an undesignated reserve
and is kept so the ruling's cost is visible rather than quietly overwritten.

Those are not five breaches. `risk-limits.md` now carries the precedence rule
and the identity behind it — `reserve − cash = invested − TOTAL = $2,385.47`,
true by construction — which reduces the whole table to one sentence:
**$2,385.47 of the designated emergency fund is sitting in equities instead of
cash.** While INVESTED exceeds TOTAL the per-name caps are suppressed, because
five labels on one scalar is not five findings. The cure is to route the next
$2,385.47 of contributions to cash; nothing here obliges a sale.

He took the option that makes his own book look worst, having been shown that it
would. That is worth recording, because the value of a pre-stated limit is
entirely in whether it still binds once it costs something.

## 6. Where the independent review was wrong, and why that is in this document

A risk-manager agent was given this question cold and reached the same split
independently, with the same cash-direction test. That agreement is the main
reason the ruling is stated as a ruling. Its report also contained four errors,
recorded here because **an error-free summary of a review that had errors would
make the agreement look stronger than it is** — which is error Mode 5 in
`error-modes.md`, the error that agrees with you.

1. **"Accounts ••••2596 and ••••6224 are `agentic_allowed: false` — I cannot
   read their positions, so ~$6,240 of the book is opaque."** It conflated
   *cannot trade* with *cannot read*. Reads succeed on all three accounts; only
   execution is restricted. Its book figures were therefore built from a repo
   file rather than the broker, and its cash number ($4,093.63) was wrong by
   $585.90 — exactly the proceeds of tonight's BTC sale. Every figure in §1
   and §4 of this document is a live read instead.
2. **"This is the third flip to 100% cash, not the second."** The log holds ten
   posts and exactly two are 100% cash: 2026-10-16 and 2026-10-19. The
   2026-10-09 post was 31.8% cash. Two.
3. **"−11.5% on deployed capital"** treats $880.30 as the deployed base
   throughout, but deployment changed four times in ten days — the 2026-10-18
   re-entry deployed about $603. There is no single "deployed" denominator, and
   a return quoted against one needs a time-weighted average deployment and must
   say so. This is the same mistake the document exists to prevent, made inside
   the review of it.
4. **"The $2,500 deposit that vanished on 10-06 is unresolved, and it sits in
   the cash leg — 30% of total."** The conclusion stands; **the reason I first
   gave for it was wrong, and is corrected here on 2026-10-20.** I wrote that
   the claim rested on misreading `FREEDOM-PLAN.md:181`, which describes $2,500
   *arriving* as planned Schwab Roth funding. But the Morning Market Monitor
   routine carries an independent, earlier record: "a $2,500 deposit went
   pending 2026-10-06 and vanished 2026-10-08 without crediting. Cause
   unresolved." So the event is most likely real and I was wrong to attribute
   the agent's claim to a misread.
   What is still wrong is the part the objection needed: a deposit that never
   credited is **not in the cash balance**, so it cannot be inflating the cash
   denominator by 30%. `pending_deposits` is 0 on all three accounts and the
   $4,679.53 of cash is settled money. The objection fails because the money is
   absent, not because the record of it was a misreading.
   **Recording this because a right conclusion reached by a wrong route is
   exactly Mode 5** — and catching it in my own correction of someone else's
   error is the most flattering possible place for it to hide.

What survived review intact: the split itself, the cash-direction test, the
observation that "total less designated reserve" is where the real ambiguity
lives, the mandate-versus-position framing of the crypto cap, and the
instruction to cure the floor by addition rather than by selling. Those are the
parts of its report that are load-bearing here, and they are load-bearing
because they were checked.

---

## 7. Operational consequence

Starting tonight the routines print **one** number per limit, against the base
named in §2, and `risk-limits.md` carries the base in the table itself. A
percentage without a stated base does not get printed at all — it raises, the
same way an unpriceable leg raises rather than being valued at zero.
