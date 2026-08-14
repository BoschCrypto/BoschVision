# Roadmap

Written overnight, 14 Aug 2026. Read the first section before the rest — it
determines whether the rest is worth doing.

---

## 1. The arithmetic, stated plainly

You asked for a system that produces alpha, on the way to $1M by 30. I ran
the numbers rather than guessing. Starting from $5,000:

**Return required to reach $1,000,000**

| Monthly contribution | in 5 years | in 8 years | in 10 years |
|---|---|---|---|
| $0 | **111%/yr** | 68%/yr | 54%/yr |
| $250 | 101%/yr | 59%/yr | 45%/yr |
| $500 | 93%/yr | 53%/yr | 40%/yr |
| $1,000 | 81%/yr | 44%/yr | 33%/yr |
| $2,000 | 66%/yr | 33%/yr | 24%/yr |

**For context, the best sustained records that have ever existed:**

| | annualised |
|---|---|
| Renaissance Medallion (gross, closed to outsiders) | 66% |
| Peter Lynch, Magellan 1977–90 | 29% |
| Warren Buffett, 1965–2023 | 19.8% |
| S&P 500, long run | ~10% |

Every cell in the first table that would get you to $1M inside 8 years
requires beating Buffett's lifetime record — most require beating Medallion,
the best fund in financial history, which had a team of PhDs, proprietary
data, and closed to outside money because it could not scale.

**This is not a pessimism problem. It is an arithmetic one.** No system I
build changes those numbers.

### What the same money does at realistic returns

At 10%/yr, starting from $5,000:

| Monthly | 5 yrs | 10 yrs | 15 yrs | 20 yrs |
|---|---|---|---|---|
| $250 | $27,586 | $64,746 | $125,887 | $226,483 |
| $500 | $46,945 | $115,958 | $229,505 | $416,325 |
| $1,000 | $85,664 | $218,380 | $436,740 | $796,009 |
| $2,000 | $163,101 | $423,225 | $851,210 | **$1,555,378** |
| $3,000 | $240,538 | $628,070 | **$1,265,681** | $2,314,747 |

**Years to $1M at market returns:** $500/mo → 28.5 yrs · $1,000/mo → 22.5 yrs
· $2,000/mo → 16.5 yrs · $3,000/mo → 13.5 yrs · $5,000/mo → 10 yrs

Notice what actually moves the outcome. Going from $500/mo to $2,000/mo cuts
12 years off. That is a far larger effect than any plausible improvement in
investment skill — and it is under your direct control, which returns are
not.

**The honest conclusion: at your capital level, income and savings rate are
the entire game.** Investment skill starts mattering once the account is
large enough that percentage returns move real dollars. Getting there is a
savings problem, not a trading problem. Build the skill now — it compounds
too — but do not expect the account to.

---

## 2. What I built tonight, and what it is for

I did not build an alpha generator, because that is not a thing that can be
built to order. I built the two things that *are* achievable and that
genuinely separate investors who compound from those who do not.

### A. An investment committee (`.claude/agents/`)

Ten specialist agents wired to your research skills, structured like a real
asset manager. Your org chart, implemented:

- **cio** — master orchestrator, runs the pipeline, writes the decision memo
- **equity-analyst**, **quant-analyst**, **macro-strategist**,
  **special-situations**, **setup-scanner** — the research desk
- **valuation-analyst** — intrinsic value and what the price already implies
- **red-team** — attacks every thesis before capital moves *(hard gate)*
- **risk-manager** — sets size, has veto authority *(hard gate)*
- **behavioral-coach** — catches FOMO/revenge/anchoring *(hard gate)*
- **portfolio-manager** — the whole book, not one position

Run it: `Use the cio agent to run a full review on ASTS`

**On the "breakout scanner" specifically.** You asked for agents that
identify when a stock is primed to break out. I built `setup-scanner`, but
it does *not* claim to predict breakouts, because nothing can. It reports
conditions that historically preceded above-average volatility, **with the
base rate including the failure rate**, and dated catalysts where a
repricing is genuinely likely — without pretending to know the direction. A
scan output reads *"54% resolved upward, median +8%, median adverse -6% —
this is a coin flip with a catalyst,"* not *"primed to run."* That is the
honest version of what you asked for, and it is still useful: it produces a
research queue.

### B. Honest measurement

This is the part that will actually save you money.

- **`hf-bot backtest` now always reports benchmarks.** Every result is shown
  against buy-and-hold of the same stock *and* SPY, with excess return and
  time-in-market. Your NVDA result (55.78% CAGR) was unreadable without
  this — NVDA itself roughly 8–10×'d over that window, so the strategy very
  likely destroyed value versus simply holding it.
- **Sample-size warnings.** Under 30 trades, results cannot distinguish
  skill from luck, and the output now says so. Your NVDA test had 15.
- **`hf-bot sweep`** runs every strategy across a mixed 24-symbol universe
  (deliberately including losers — NKE, INTC, PYPL, PFE, BA — because a
  universe of winners is survivorship bias) and reports the hit rate versus
  buy-and-hold. This is the test that tells you whether any of it works.
- **`hf-bot journal`** — no BUY can be recorded without written falsification
  criteria. The `scorecard` tracks accuracy, **discipline** (did you exit
  when your own rules said to?), and whether your stated conviction carries
  any information at all.

---

## 3. Run this first, in the morning

```bash
cd BoschVision
git pull
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# The honest test. ~5 minutes.
hf-bot sweep --start 2021-01-01
```

That single command answers the question everything else depends on: **do
these strategies beat buying and holding?** I could not run it here — this
sandbox blocks all market-data hosts — so it has to be you.

If the hit rate is at or below ~50%, the strategies are noise, and the
correct response is to stop trading them and put the money in an index fund
while you build skill on paper. That would be a genuinely valuable result,
not a failure.

Then re-run your NVDA test with benchmarks now visible:
```bash
hf-bot backtest NVDA --strategy momentum_90d --start 2022-01-01
```

---

## 4. Sequenced plan

**Phase 1 — Find out if anything works (this week)**
Run `sweep`. Run backtests with benchmarks. Use `quant-analyst` to audit the
results for overfitting and multiple-testing bias. *Decide based on evidence
whether systematic trading is worth continuing at all.*

**Phase 2 — Paper trade honestly (1–3 months)**
Whatever survives Phase 1, run on your dedicated Alpaca paper account with
the journal enforced. Log every decision with falsification criteria.
Track versus SPY. Three months of disciplined paper trading with a written
record teaches more than a year of unrecorded live trading — and costs
nothing.

**Phase 3 — Use the committee for real research (ongoing, start now)**
Independent of the bot, run the `cio` pipeline on companies you genuinely
want to own. This builds durable skill. Where retail actually has structural
edge — small caps below institutional size limits, spin-offs, long holding
periods, no redemption pressure, no career risk — is exactly where this
committee is pointed. That edge is real. Day-trading edge is not.

**Phase 4 — Scale only what is proven (6+ months out)**
Deploy real money only into what cleared Phases 1–2, sized by the
risk-manager's rules (1–2% risk per trade, not the 7% we sketched earlier).
Keep the core in an index fund; cap the active sleeve so its total failure
cannot derail the plan.

**Phase 5 — The actual wealth engine (in parallel, most important)**
Income growth and savings rate. Per the table above, moving from $500/mo to
$2,000/mo saved is worth more than 12 years of the best trading you could
realistically achieve. Also: max any employer 401(k) match first — an instant
50–100% return that no strategy in this repo can approach.

---

## 5. What this system honestly offers

**It will:** enforce written theses and pre-committed exits; attack your
ideas before they cost you; size positions so a losing streak is survivable;
catch revenge trading and FOMO; measure you honestly against a benchmark;
and build genuine investing skill with an audit trail.

**It will not:** predict prices, generate alpha, or make $5,000 into
$1,000,000 in five years.

The measurable value is in **errors not made** — positions not oversized,
theses not entered untested, losses not revenge-traded, index funds not
abandoned for a strategy that never beat them. Over a decade, avoiding those
mistakes is worth more than any signal, and it is the edge genuinely
available to you.

You are young and you are building real skill. That combination is worth a
great deal — just on a longer timeline and through a different mechanism
than the one you asked for. I would rather tell you that now than let you
find out with money.
