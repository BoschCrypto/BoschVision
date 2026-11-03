# Recurring error modes in this project, and the checks that catch them

Written 2026-09-24 after a session in which six confident statements were wrong.
They were not six separate mistakes; they were two mechanisms firing repeatedly.
Both checks below are cheap. Neither depends on remembering to be careful.

---

## Mode 1 — reading a LEVEL where the DERIVATIVE is the decision

| Claim | Level seen | Derivative ignored |
|---|---|---|
| NVO "most interesting" (2026-09-23) | GM 80.6%, ROE 59.8% | GM 84.7% → 81.0% → 78.2% over six quarters |
| LULU "interesting" at 9.9x fwd | ROE 30.9% | GP/A 82.5% → 74.3%; CFO −29%; Americas comps −12% |
| Memory cohort "standout insight" | Net margin 53–65% | Peak-cycle; 52-wk highs June, then −25% to −47% |
| "Regime RISK-ON" reported 15 straight sessions | SPY > 10-month SMA | The gate was never measured against its own removal |

The sharper form: **in every case the cause of the flattering level was the force
that would destroy it.** NVO's margin came from pricing already legislated away
(MFP: Ozempic $274/mo from 2027-01-01). LULU's ROE was flattered by equity
shrinking faster than earnings. Memory's margins ARE the AI-capex peak — the exact
mirror of NVDA's guided −300bp compression, analysed correctly the same day
without the connection being made. Even DG, the survivor: its +3.5% comp is
high-income trade-down, i.e. a recession signal rather than durable share gain.

**CHECK — before any name advances from a screen:** pull 3+ periods of the
specific metric that caused it to screen, from filings, not from a vendor ratio
field. If the slope is negative, the level is a lagging measurement of a condition
that has already changed. A high margin falling 8 points in a year is a moat being
tested, not a moat.

Corollary: vendor forward P/E and PEG are built on analyst estimates, and analysts
are most optimistic on exactly the names that screen cheapest. Any PEG below ~0.3
is a data artifact to verify, not a finding.

---

## Mode 1b — a metric FLOOR on a cyclical is a cycle-position filter, not a quality filter

Discovered 2026-09-24 on DE (Deere), and it is the mirror image of Mode 1 rather
than another instance of it. Mode 1 is a flattering level on a negative slope.
This is a **trough level passing a quality threshold, then paid for at a peak
multiple**.

A screen requiring `net margin > 10%` and `ROE > 15%` passed DE here:

| FY | Revenue | Net income | Net margin |
|---|---|---|---|
| FY23 | $61.251B | $10.166B | **16.60%** |
| FY24 | $51.716B | $7.100B | 13.73% |
| FY25 | $45.684B | $5.027B | 11.00% |
| TTM (to 2026-08-02) | $47.983B | $4.873B | **10.16%** |

Revenue −25.4% peak-to-FY25, net income −52.1%. ROE 18.4% against roughly 35% in
FY23. The filter did not select a high-quality business; it selected a cyclical
that had fallen just far enough to still clear the bar.

> **CHECK:** before treating a margin/ROE screen hit as quality, locate the metric
> inside that company's own 5-year range. On a secular business a floor is a
> quality filter. On a cyclical it is a cycle-position filter, and it fires
> precisely at the trough.

Two companions to the same check, both missed on DE:

- **Read the trailing multiple, not only the forward one.** The DE case quoted
  forward P/E 28.65 and never trailing **39.4x**, nor P/B **6.83** (VTI: 27.37x /
  5.02x). On trough earnings a forward multiple silently embeds the recovery — DE
  at $706 required FY27 EPS ~$24.64 against FY26 guided ~$18.00, i.e. **+37% EPS
  growth already in the price** after a 63% run off the low.
- **Decompose ROE before believing it.** DE's Financial Services segment is
  $70.300B of $107.607B total assets (**65.3%**) at 3.84x leverage. The "18.4%
  ROE" is a captive lender's levered ROE bolted onto a manufacturer, and an
  `ROE > 15%` filter cannot distinguish that from an unlevered industrial earning
  the same number. Worse, the provision was falling (−20.5% y/y) into USDA net farm
  income −5.5% in real terms — flattering current EPS.

---

## Mode 3 — proposing something whose only support is a signal this project already measured as worthless

The DE proposal's single stated positive was *"new 52-week high, the only genuine
breakout in the cohort."*

**That is the exact condition measured as negative-expectancy and formally banned
as a screen 24 hours earlier** (2026-09-23, corroborated twice: deleting the
52-week filter improved the cross-sectional strategy 352% → 512%; and standalone
proximity spreads of −0.33% / −0.60% / −0.82% at 1/3/6m with CIs excluding zero).
The ban was written into the memory ledger by the same agent that then advanced the
proposal.

This is not an analytical error. It is a **failure to apply the project's own
findings to the project's own proposal** — the measurement existed, was recent,
was recorded, and was not consulted.

> **CHECK:** before proposing a position, state the signal that generated it and
> run `hf-bot memory recall` against that signal. If the ledger contains a measured
> verdict on it, that verdict governs. A finding that is not consulted at decision
> time is not a finding, it is a note.

---

## Mode 2 — asserting absence from a search that cannot detect presence

Two claims, both stated with confidence, both wrong, both the same logic:

- **"The backtest is blocked on data."** Established that yfinance was
  proxy-blocked, then generalised to *market data is unreachable*. Never enumerated
  alternatives — while calling `get_equity_historicals` every morning for quotes.
  Cost: three weeks of a stalled project and 15 daily reports of a blocker that
  did not exist.
- **"The harness was never committed and is lost."** Ran `git log --all` and
  `git cat-file` against a STALE clone. `--all` searches local refs only; the
  `origin/` pointer predated all seven commits. Never ran `git fetch`. Told the
  principal that weeks of work was destroyed, on a check structurally incapable of
  finding it.

**CHECK — before stating that something does not exist:** name the search that
WOULD detect it, confirm the search you ran is that search, then run it.
Concretely: `git fetch` before `git log --all`; enumerate every reachable data
source before declaring a data blocker; distinguish "absent from my copy" from
"absent."

---

## Where weight belongs

Every correction in that session came from measurement against a control, or from
primary sources. None came from better intuition:

- the regime gate died to an ablation
- momentum continuation died to a date-matched baseline (6m hit rate 64.6% vs
  65.6% unconditional — see FINDINGS.md)
- NVO died to a reverse DCF
- the memory cohort died to volume ratios and distance-to-support

Verification work in the same session held up: caught a screen's market cap
overstated 7%, DG's untagged capex, a scanner's two-week-stale macro backdrop, a
PBR/PBR.A duplicate, pre-market spreads misread as real, and a Fed hike the
monitor had structurally missed.

**The errors cluster in generative judgement ("what looks interesting"), not in
verification judgement ("is this number right").** Operating consequence: generate
candidates and attack them with primary sources and controls in the SAME step,
rather than reporting enthusiasm and attacking afterwards. Three of the six errors
were preceded by a superlative — "most interesting", "standout". Treat that phrase
as a signal to slow down, not as a conclusion.

---

# Second pass, 2026-10-14

Added after auditing the signal tracker rather than reasoning about it. Nine
defects, all demonstrated against the live log before being touched. They are
three mechanisms, not nine mistakes, and two of them are already in the file
above wearing different clothes.

---

## Mode 4 — a wrong number arriving with no error raised

One primitive, `_value()`, skipped any holding it could not price (`if a in
prices`). A missing price therefore became a sleeve quietly short by that
holding's entire value. Five call sites hit it in three days:

| Where | What it would have reported |
|---|---|
| `frozen()` | a benchmark short by a whole leg |
| control arm | **raised** — the only one that failed loudly, and so the only one found on the day |
| `follow()` | **$490.55 against a true ~$990**, live, silently |
| `value_position()` | a duplicate guard with a *different* rule (any NAMED asset vs any asset HELD), so a zero-unit leftover raised from one path and valued cleanly from the other |
| `hold()` | `KeyError` the day Adam drops BTC — the benchmark arm dying because the signal stopped mentioning its asset |

The `follow()` instance stayed off the real log by luck: Monday's screenshot
showed four lines and no SOL, and it was transcribed with an explicit `SOL: 0.0`
instead of literally. A faithful transcription would have made the arm wrong.

> **CHECK — prefer the loud failure, every time.** Between a function that
> raises and one that returns a plausible number, take the raise. The control
> arm's crash cost ten minutes. Its silent siblings would have cost weeks of a
> wrong benchmark that nobody had reason to re-derive.
>
> **And one rule per question.** Two guards asking the same thing drift apart;
> that is what they do. Delete the copy, keep the primitive strict.

Corollary, learned the same day: **an asset absent from a published set is at
zero, not unchanged.** The weights sum to 100% with cash named, so there is no
room left for a holding the post omits. Reading an omission as "no change" is
how a sold-out leg stayed in a book.

---

## Mode 5 — the error that agrees with you is the one that survives

This is the mode with the worst record and no code fix. Six instances, one
mechanism: **a result was not re-derived because it pointed where I was already
pointing.**

| Error | Direction it ran |
|---|---|
| `pending_deposits` added to `total_value` | flattered the portfolio (29% inflation; every concentration read safer) |
| age stated wrong | flattered the portfolio |
| `follow()` charged the full spread per leg | flattered my scepticism of the paid signal (made it look 2x as expensive) |
| "a missed forward understates the cost of following" | flattered my scepticism — and was **false**: closing the gap moved the follower +$2.89, because a skipped rebalance removes its cost *and* its effect, and the effect dominates |
| `follow()`'s cost parked as negative cash, escaping every price move | flattered the paid signal by $9.45 on a doubling — the one that ran the *other* way, and it sat unfound longest |
| a duplicate log reading as a free no-change | flattered the signal: implied drag 11.7%/yr → 9.4%/yr, $30/yr cheaper |

Note the fifth row. Four errors ran toward scepticism, two toward the signal,
and the one that contradicted my prior survived longest of all — which rules out
"I am biased against the subscription" and leaves the plainer reading: **whatever
agrees with the last thing I concluded does not get checked.**

> **CHECK — when a result agrees with you, re-derive it a second way before
> reporting it.** Cheap version: before saying "X would cause Y", compute X both
> ways. That took one script and killed a claim I had made twice.
>
> This is also why `knowledge/process/hypotheses.md` exists: a prediction
> written down *before* the data arrives cannot be quietly reshaped by it.

---

## Mode 6 — a percentage with no stated base

Four instances, all the same missing noun.

- `pending_deposits` inside `total_value` — inflated the denominator 29%.
- the crypto sleeve used as the base for whole-signal weights, **twice** —
  reported BTC 12.9pp off target when the real drift was 2.1pp.
- the risk limits in `knowledge/process/risk-limits.md` still do not name their
  base, which is why tech reads 23% one way and 43% the other. Unresolved, and
  Waldo's call, not mine.

> **CHECK — no bare percentage.** Every figure carries its base in the same
> sentence it appears in. `total_value` already excludes `pending_deposits`;
> signal weights sum to 100% *with cash as a named slice*, so the invested
> sleeve is never the denominator.

---

## What actually caught these

Not care. Of the nine, **five were found by computing something that had been
asserted**, and the audit that found the live `follow()` bug was mechanical:
grep every call site of the primitive that had already failed twice, and check
each one. No judgement involved.

The two that intuition did find were found while *sizing a real trade* — doing
the arithmetic on live money, not reviewing code.

Consistent with "Where weight belongs" above: the errors are in generative
judgement, the saves are in verification. Operating consequence for this
project specifically: **when a bug is found, grep for its shape rather than
fixing the instance.** Every one of the five `_value` call sites was the same
bug; four were still live after the first was fixed.
