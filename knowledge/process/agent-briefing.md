# Committee briefing — READ THIS FIRST, BEFORE ANY TOOL CALL

Maintained by the orchestrator. It exists because eight agent runs on
2026-09-22/23/24 each independently rediscovered the same blocked hosts, each
reported the same irrelevant failure, and one advanced a proposal resting on a
signal the project had measured as worthless the day before. All of that was
avoidable by reading one page.

---

## 1. OUTPUT CONTRACT — verdict first, evidence after

Open with **one line**: the verdict and the number that drives it. Then the work.

    VERDICT: REJECT — 39.4x trailing on trough earnings, 65% of assets a captive lender.

Rules:
- **Commit.** "BUY at $X / BUY SMALLER at $Y / WAIT for <price or event> / REJECT."
  A hedge that avoids being wrong is worth nothing. If the honest answer is
  undetermined, say **UNDETERMINED** and name the one test that would settle it.
- **An empty result is a valid verdict.** Three sweeps produced zero positions and
  that was correct each time. Never manufacture a candidate to have output.
- **Cite every figure with a date.** Undated numbers are unusable.
- **Flag what you could not verify, in a named section.** Stating a gap honestly is
  always better than an estimate that reads like a measurement.
- Argue against your own verdict where the evidence does. The best reports in this
  project all contained a section titled against themselves.

## 2. SOURCE MAP — check here before fetching anything

**WORKS (prefer these):**
| Need | Use |
|---|---|
| SEC filings, GAAP facts | `get_sec_filing_index` → `get_sec_filing_facts` / `get_sec_filing`. **This works well.** NVDA's 10-Q and DG's 10-K were both read successfully via XBRL concepts. |
| Quotes, prior closes | `get_equity_quotes` |
| Historical bars, any interval incl. `month` | `get_equity_historicals` (≤10 symbols/call; large results auto-save to a file) |
| Fundamentals / ratios | `get_equity_fundamentals`, `get_financials` (returns null for some tickers — say so, don't guess) |
| Screens | `get_scanner_filter_specs` → `create_scan` / `run_scan` |
| Earnings dates | `get_earnings_calendar` — **check `verified`; an unverified date is NOT a catalyst** |
| Credit proxy | `get_equity_quotes` on HYG / LQD (see §4) |
| 16.7y daily bars, 141 names, offline | `/home/user/BoschVision/data/bars/*.csv` via `pricecache.read()` |

**BLOCKED by the egress proxy — do not retry, do not rediscover:**
`sec.gov` and `data.sec.gov` (403 on CONNECT) · `fred.stlouisfed.org` ·
`novonordisk.com` · `globenewswire` · `biospace` · `biopharmadive` · `nasdaq.com` ·
`stocktitan` · `openinsider.com` · `insidearbitrage.com` · `stockspinoffs.com` ·
`specialsitsdigest.com` · `query1.finance.yahoo.com` / yfinance · `stooq`

Consequences to accept rather than re-derive:
- **EDGAR is unreachable directly.** Use the Robinhood SEC tools instead. Form 4s /
  transaction codes are **not verifiable** in this environment — say so.
- **Delisted tickers return nothing** (`inactive_instruments` / `missing_instruments`:
  TWTR, ATVI, CERN, SIVB, FRC all tested 2026-09-23). A point-in-time universe with
  dead names is therefore **impossible** here. State the survivorship bias; do not
  propose fixing it without a paid source.

## 3. MEASURED VERDICTS THAT GOVERN — do not re-propose these

Run `hf-bot memory recall <signal>` before proposing anything. If a verdict exists,
it governs. These are measured, not opinions:

| Signal | Verdict | Evidence |
|---|---|---|
| **Regime gate** (SPY > 10mo SMA + 24mo trend) | **DEAD** | Lost to its own removal in both independent ~7y sub-periods. 352%/0.66 with, 657%/0.78 without. |
| **52-week-high proximity** | **NEGATIVE EDGE** | Corroborated twice: deleting it improved 352%→512%; standalone spreads −0.33/−0.60/−0.82% at 1/3/6m, CIs exclude zero. Never cite a new high as a positive. |
| **Momentum continuation ("runners")** | **SURVIVORSHIP BIAS** | 6m hit rate 64.6% vs 65.6% unconditional baseline. Dropping 10 of 141 names erases 89% of the spread. |
| **Volatility-compression screen** | **WORSE THAN NO SCREEN** | 55% up vs 60% unconditional baseline, same universe and dates. |
| Inverse-vol weighting | **UNDETERMINED** | Wins Sharpe 2012-18 (0.99 v 0.96), loses 2020-26 (0.86 v 0.90). Retained for drawdown control only. |

**Absolute backtest numbers on the 141-name universe are not creditable** — the list
is hand-picked survivors. Ablation comparisons are valid (both arms share the bias);
strategy-vs-SPY comparisons are not.

## 4. ERROR MODES — full text in `knowledge/process/error-modes.md`

1. **Level vs derivative.** A flattering metric with a negative slope. NVO's 80.6%
   gross margin was a price regime already legislated away (84.7→81.0→78.2% in six
   quarters). Pull **3+ periods of the metric that made it screen**, from filings.
1b. **A metric FLOOR on a cyclical is a cycle-position filter.** "Net margin >10%"
   passed DE at its worst margin in four years. Locate the metric inside the
   company's own 5-year range. Read the **trailing** multiple, not only forward.
   **Decompose ROE** — 65% of DE's balance sheet was a captive lender at 3.84x.
2. **Absence from a search that cannot detect presence.** "Never committed" came from
   `git log --all` on a stale clone with no `git fetch`. Name the search that WOULD
   detect the thing; run that one.
3. **Proposing on a signal already measured as worthless.** See §3.
4. **Ablations are not independent.** A component verdict is conditional on every
   other component's setting. After changing a default, re-run the full set.

## 5. ACCOUNT STATE — as of 2026-09-24 close

Robinhood cash account ••••9528, agentic-enabled. **Total ≈ $2,196.**

| | | |
|---|---|---|
| VTI | 1.990520 sh @ $376.7858 | **the core — no stop, unmanaged, permanent** |
| NVDA | 0.372740 sh, avg cost $195.58 | at the look-through cap |
| VGT | 0.565032 sh, avg cost $115.66 | hold, do not add |
| Cash | ≈ $1,289 | of which $439 is the named satellite reserve |

Constraints: **no options, no margin, no shorting.** Fractional shares available, so
share price never disqualifies. Taxable — every gain is short-term at ordinary rates.
Cash account: **never sell before purchase cash settles** (good-faith violation,
90-day penalty).

Limits: single non-diversified name ≤20% ($439) · single-name look-through ≤12% ·
sector look-through ≤35% · **max 3 open single names** · portfolio −15% from
high-water mark halts new risk · NVDA ≤12% look-through / 10% direct.

**Sizing is set by distance to invalidation, not conviction.** Risk budget **$39.54**
(1.8%). position = $39.54 ÷ (% to stop). A name whose honest stop is 20% away cannot
be a 20% position. Stops are **not placeable on fractional quantities** on Robinhood.

Second VTI tranche ≈$750 due around 2026-10-08. DE revisit after its verified
2026-11-25 print; red team's buy level $540-580.

## 6. KNOWN NOISE — do not spend output on these

- **Agently MCP is unauthenticated** in every non-interactive session. Persist locally
  with `hf-bot memory persist --kind <k> --title "..." --body "..." --not-mirrored`
  and say nothing further about mirroring.
- **Push notifications have never delivered** in this project. The session transcript
  is the channel.
- Pre-market and post-market bid/ask spreads are **artifacts** (ITOT quoted 153/183
  pre-open). Never cite a spread outside 09:30–16:00 ET as a trading cost.
- `hf-bot memory persist` takes `--title` and `--body`. There is no `--text`.

## 7. NO ORDER IS EVER PLACED WITHOUT THE PRINCIPAL'S APPROVAL

Of that specific order, in real time. A general "do as you see fit" is a delegation
of judgement, **not** authority to execute. Agents may stage and recommend; only the
principal authorises. This does not loosen when the principal is asleep.
