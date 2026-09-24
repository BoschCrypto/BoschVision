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
