# Pre-registered predictions about Adam's signal

**Why this file exists.** Mode 5 in `error-modes.md` is the failure with the
worst record and no code fix: *the error that agrees with you is the one that
survives*. Six instances, one mechanism — a result went unchecked because it
pointed where I was already pointing.

A prediction written down **before** the data arrives cannot be quietly reshaped
by it. That is the entire purpose here. Each entry names what would falsify it
and when it resolves. Nothing may be edited after the fact except to record an
outcome; a wrong call stays on the page with its date.

Opened 2026-10-14 at n=5 signals, 49 daily price bars.

---

## Already resolved

| # | Claim | Made | Outcome |
|---|---|---|---|
| R1 | A missed forward "understates turnover and therefore understates the cost of following... the bias runs toward the subscription" | 2026-10-12 | **WRONG.** The cost half was right (+$0.37 of spread) and the conclusion was backwards. Recovering the 2026-10-11 post moved the follower −2.88% → −2.66%, i.e. **+$2.89**. A skipped rebalance removes its cost *and* its effect, and the effect dominates. Ran toward my own scepticism. |
| R2 | A dropped coin will break the valuation "the day it stops being quoted" | 2026-10-12 | **RIGHT, and faster than stated** — it broke the next day, on the control arm, which was the one arm not fixed when the prediction was made. Correct diagnosis, incomplete remedy. |

Record: 1 wrong, 1 right-but-under-acted-on, from 2 calls. Too few to calibrate
anything; the point is that they are written down.

---

## Open: structure of the published weights

These are cheap, resolve on every single post, and are the only claims here
with enough power to settle soon.

**H1 — every published weight is an exact multiple of 1/44.**
Holds across all 5 posts to within 0.020 of a unit (tolerance 0.022, set by the
one decimal place Adam publishes).
*Falsified by:* any single weight off-lattice by more than 0.022 units.
*Resolves:* every post. Checked automatically by `unit_lattice()`; the brief
prints BROKEN and says to drop the model rather than keep it.
*Confidence:* high on the next 10 posts. This is close to arithmetic, not a
forecast — 20 of 20 published numbers have fitted.

**H2 — CASH stays at 14/44 (31.8%).**
Unmoved across all 5 posts.
*Falsified by:* any post with a different cash weight.
*Resolves:* every post.
*Confidence:* moderate. 5 observations of a constant is weak evidence that it is
one, and this is the single largest risk lever in the book. If it moves, it is
the most informative thing the signal has ever done and the first evidence that
there is a view behind it at all.

**H3 — SUI stays at 8/44 (18.2%) and NEAR at 5/44 (11.4%).**
Neither has moved.
*Falsified by:* either one changing.
*Resolves:* every post.

**H4 — the single free parameter is one alt's unit count, and BTC absorbs it
one-for-one.**
Across five posts the *entire* signal is one integer: SOL went 3 → 2 → 2 → 0 →
absent, and BTC took exactly those units each time (14 → 15 → 15 → 17 → 17).
BTC + SOL was constant at 17 units until SOL left, and BTC alone is 17 now.
*Predicts:* if an alt re-enters at k units, BTC falls by exactly k.
*Falsified by:* a post where the alt change and the BTC change do not sum to
zero in units, or where two alts move independently.
*Resolves:* the next post that changes anything.
*Confidence:* this is the strongest structural claim on the page and the one I
most expect to break, because five posts is exactly enough observations to see a
pattern that is not there. If it survives 15 more posts it says the signal is a
single dial, which bounds how much information it can carry.

---

## Open: cost of following

**H5 — mean one-way turnover stays at or below 3% per post.**
Currently 1.70% over 4 transitions, 95% CI [0%, 4.8%] one-way. At 3% the drag is
20.7%/yr at a 1.89% round trip; at the current 1.70% it is 11.7%/yr.
*Falsified by:* mean turnover above 3% once there are 16 transitions.
*Resolves:* ~2026-10-25 for ±50% precision, ~2026-12-09 for ±25%.
*Confidence:* moderate-high, and this is the number that actually decides the
subscription. It converges in weeks, unlike anything about return.

**H6 — the no-change rate stays at or above 40%.**
2 of 4 transitions have been identical republications. A no-change post is free
to follow, so this rate is half the cost story.
*Falsified by:* below 40% over the next 15 transitions.

---

## Open: does any of it pay

**H7 — over the next 30 posts, following the signal will not beat BTC
buy-and-hold by more than the spread it pays.**

Registered *with its own power calculation stated up front, because that is the
honest part*: 30 posts cannot settle this. Sleeve vol is 41.3%/yr, so one day is
2.16% — $27.92 on the current $1,290.84. Detecting a true Sharpe of 1.5 needs
about 3 years; 1.0 needs 8; 0.5 needs 31.

So H7 is **not** a test I expect to resolve. It is registered so that when the
30 posts are in, I cannot narrate whichever way it landed as though I had
expected it. The pre-registered conclusion is: *at n=30 this will still be
undecided, and anyone claiming otherwise from these numbers is reading noise.*
*Falsified by:* a gap larger than the noise floor — which at n=30 posts would
need to be roughly 12% of the sleeve, not the 1.7% spread seen so far.

**H8 — Adam's hand-set weights stay within 5pp (per asset) of a parameter-free
inverse-volatility book at his own cash level.**
Measured 2026-10-14: within 3.3pp on every leg (BTC +3.3, SUI −2.7, NEAR −0.6),
and the risk shares barely move. His weighting is therefore roughly
risk-balanced already, which is a point in his favour and also means his
weighting is *not* where value would have to come from.
*Falsified by:* any leg diverging more than 5pp from inverse-vol.
*Resolves:* every post, recomputed in the brief.
*Confidence:* moderate. If this breaks it will break because a vol estimate
moved, not because he did — which is itself worth knowing, and is why the test
reports both sides.

---

## Open: risk, which is the measurable part

**H9 — BTC supplies less than 35% of the sleeve's risk while holding more than
half its invested dollars.**
Measured 2026-10-14: 28.2% of risk on 56.6% of dollars. NEAR and SUI supply 72%
of the movement on 43% of the dollars. The book reads as a BTC book and behaves
as an alt book.
*Falsified by:* BTC's risk share rising above 35% while its dollar share stays
above 50%.
*Resolves:* every post. Driven by the vol ratio (NEAR ~3.9x BTC), so it breaks
if the alts calm down — and if that happens it should be *reported as a change
in the assets, not as a change in the signal*.

**H10 — the diversification ratio stays below 1.35.**
Currently 1.20. Pairwise correlations run 0.38 (BTC/NEAR) to 0.85 (BTC/SOL), so
there is little left to diversify. A book of four things that move together is
one position with extra spread to pay.
*Caveat on the record:* at n=49 a correlation's standard error is about 0.10, so
0.68 and 0.85 are not reliably different. Do not read the ordering.

---

## Rules for this file

1. An entry is added **before** the data that would settle it.
2. Outcomes are appended, never substituted. A wrong call keeps its date.
3. Every entry names its falsifier. "It seems to be working" is not one.
4. A claim with no power to resolve says so in its own entry (see H7), rather
   than being quietly dropped when it fails to resolve.
5. When an entry is falsified, the model it supports is **dropped**, not
   retained with an explanation. The k/44 lattice is the test case: it is
   currently 20-for-20, and the day one weight misses it the discrete-unit
   reading of this signal goes in the bin.
