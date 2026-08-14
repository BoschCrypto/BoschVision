# Good morning — start here

Everything below is built, tested (101 passing) and pushed. Three commands.

## 1. Update your local copy

```powershell
cd BoschVision
git pull
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 2. Run the one test that matters (~5 min)

```powershell
hf-bot sweep --start 2021-01-01
```

Runs all 5 strategies across 24 mixed symbols — deliberately including
losers (NKE, INTC, PYPL, PFE, BA, T) so winners can't flatter the result —
and reports **how often each strategy beat simply buying and holding.**

**How to read it:** a coin flip is ~50%. Below that, the strategy is noise or
worse after costs and short-term capital-gains tax. I could not run this
here — this sandbox blocks every market-data host (verified again just now,
all returning `000`) — so this is yours to run.

I don't know what it will say. If it comes back under 50%, that is a real,
valuable answer and not a failure: it means don't risk money on these, and
it will have cost you nothing to find out.

## 3. Re-run your NVDA test, now with the benchmark visible

```powershell
hf-bot backtest NVDA --strategy momentum_90d --start 2022-01-01
```

Last night this printed `CAGR: 55.78%` and nothing else — which was
unreadable, because NVDA itself roughly 8–10×'d over that window. It now
shows buy-and-hold of NVDA and SPY alongside, plus excess return, time in
market, and a sample-size warning (you had 15 trades; 30+ is where luck and
skill start to separate).

Expect this to look considerably worse than it did. That's the point.

---

## Also new: your investment committee

Ten agents in `.claude/agents/`, wired to your research skills:

```
Use the cio agent to run a full review on ASTS
Use the red-team agent to attack my thesis that NVDA is cheap
Use the risk-manager agent to size a position in AMD
Use the behavioral-coach agent — I lost on TSLA and want back in
Use the setup-scanner agent on the S&P 500
```

Three **hard gates** before any BUY: red-team must be answered, risk-manager
can veto on size, behavioural-coach runs whenever there's urgency or a recent
loss. See `.claude/agents/README.md`.

## And a decision journal

```powershell
hf-bot journal add --symbol NVDA --decision BUY --conviction medium `
  --thesis "..." --falsification "..."
hf-bot journal list
hf-bot journal scorecard
```

It **refuses** to record a BUY without falsification criteria — you decide
your exit while calm, not while a position is bleeding. The scorecard later
tells you your accuracy, your *discipline* (did you actually exit when your
own rules fired?), and whether your "high conviction" calls are any better
than your low-conviction ones.

---

## One thing to read before you trade

`ROADMAP.md` opens with the arithmetic on $1M by 30. The short version: from
$5,000, reaching $1M in 5 years needs **111%/yr** — Renaissance Medallion,
the best fund in history, did 66%. Going from $500/mo to $2,000/mo saved cuts
12 years off the timeline, which is more than any realistic improvement in
trading skill and is entirely under your control.

I built you the best version of what you asked for. I'd be doing you a
disservice if I let you run it believing it can do something it can't.
