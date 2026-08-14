# hf-trading-bot

> **New here? Read [`MORNING.md`](MORNING.md) first, then [`ROADMAP.md`](ROADMAP.md).**
> The roadmap opens with the arithmetic on what returns can and cannot do —
> it determines whether the rest of this is worth running.

## The result that shapes everything below

`hf-bot sweep` tested five systematic strategies across 24 symbols,
2021–2026. **All five lost to simply buying and holding the same stock** —
hit rates of 21–38% (a coin flip is 50%), median excess returns of −35 to
−71 points. The result was audited for look-ahead bias, cash drag, survivor
bias, transaction costs, and a real bug (open positions dropped from the
return calc) — none of it explains the gap. Full writeup: [`FINDINGS.md`](FINDINGS.md).

**This changes what this repo is for.** The systematic strategies below are
now the *measured baseline* — kept because future rules have to beat them,
not because they're expected to be traded live. `hf-bot run --live` refuses
to deploy any strategy with a recorded sub-50% hit rate unless you pass
`--i-know-this-failed-backtest` explicitly.

The recommended workflow is the **investment committee** — ten research
agents modelled on an institutional process
([`.claude/agents/`](.claude/agents/README.md)) — feeding the **decision
journal** (`hf-bot journal`), scored against the **index counterfactual**
(`hf-bot portfolio`): given the money actually deposited, on the dates it
was deposited, is the account ahead of or behind just buying SPY?

```
Use the cio agent to run a full review on <TICKER>
hf-bot journal add --symbol ... --decision BUY --falsification "..."
hf-bot portfolio contribute --amount 500
hf-bot portfolio compare          # the number that actually matters
hf-bot journal scorecard          # were past decisions actually right?
```

That loop is unfakeable in a way a backtest on hand-picked strategies isn't:
it can't be gamed by a favorable window, because it's scored against what
the same money would have done in the index, updated every time cash moves.

## Execution engine (baseline / experimental)

A pluggable strategy engine, portfolio risk management (drawdown circuit
breaker, daily/weekly loss guards, ATR-based position sizing), a
backtester, and a broker abstraction — safe simulated paper trading by
default, with explicitly opt-in Alpaca/Robinhood live adapters. This is the
infrastructure the sweep above ran on, and it stays useful for testing any
*new* hypothesis — just don't mistake "the code runs" for "the strategy
works." Five specific rules already didn't, on this evidence.

Architecturally this ports the strategy engine, risk math, and backtest
replay logic from the [Apex Trading Hub](https://apex-trading-bosch.lovable.app)
dashboard into a standalone Python bot, with two more strategies
(MACD crossover, Bollinger breakout) added.

## Read this before you do anything else

- **No strategy here is guaranteed to be profitable.** Backtested numbers
  are historical, not predictive; markets change regimes. Anyone offering a
  bot that reliably prints money is lying to you — including, implicitly,
  any hype around this repo.
- **This is not high-frequency trading.** Real HFT needs colocated servers
  and direct exchange feeds to compete on microsecond latency. This bot runs
  a systematic decision loop on a normal interval (default: hourly) — fine
  for daily-bar swing strategies, not a latency race.
- **Defaults are safe on purpose.** The bot starts in paper mode
  (`broker: paper` in config), the kill switch defaults to ON (paused), and
  live trading requires you to explicitly set an environment variable that
  says, in effect, "I understand this risks real money."
- **Robinhood has no public paper-trading sandbox.** Unlike Alpaca, there's
  no simulated Robinhood account to trade against — so "paper trading with
  Robinhood" here means: the bot's own `PaperBroker` (real market data, fake
  money, in-memory/local only) stands in for it. `RobinhoodBroker` is the
  live-money path, for later, once you trust a strategy.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Backtest a strategy against a symbol over a date range.
hf-bot backtest NVDA --strategy momentum_90d --start 2022-01-01

# Run one dry-run cycle against the default watchlist (paper broker, no writes).
hf-bot run

# Actually execute paper trades (simulated fills, simulated money — still
# 100% safe, nothing leaves your machine).
hf-bot run --live

# Loop continuously.
hf-bot loop --interval 3600 --live

# Check status / flip the kill switch.
hf-bot status
hf-bot kill-switch --off   # enable trading
hf-bot kill-switch --on    # pause (default)

# Log a deposit, then check the account against the same cash in SPY.
hf-bot portfolio contribute --amount 500
hf-bot portfolio compare

# Test a set of strategies across a universe of symbols, all at once.
hf-bot sweep
hf-bot sweep-history

# Visualize the committee as a live HUD (see "Live Agent Cortex" below).
hf-bot dashboard
```

Copy `config/settings.example.yaml` to `config/settings.yaml` to customize
the watchlist, starting cash, or broker.

## Strategies

| key                  | logic                                                                 |
|----------------------|------------------------------------------------------------------------|
| `momentum_90d`        | Entry on N-day momentum crossing negative→positive, or a sustained-trend re-entry above a rising 50-day SMA. Exit on momentum crossing back negative. |
| `rsi_mean_reversion`  | Entry when RSI crosses below `oversold`, exit when it crosses back above `overbought`. |
| `sma_crossover`       | Entry on fast SMA crossing above slow SMA (golden cross), exit on the reverse. |
| `macd_crossover`      | Entry when the MACD line crosses above its signal line, exit on the reverse. |
| `bollinger_breakout`  | Entry on a close above the upper Bollinger Band, exit on a close back below the middle band. |

All five are pure functions over an OHLCV bar series (`hf_trading_bot/strategies/`)
and are exercised identically by the live engine and the backtester — the
backtester walks the bar series bar-by-bar so both see exactly the same
signal at exactly the same point, no lookahead.

## Risk management (`hf_trading_bot/risk.py`)

- **ATR-based stop/target sizing** — stop = `entry - atr_multiple × ATR(14)`,
  clamped so it never exceeds `stop_loss_pct` of entry; target = `entry + take_profit_r × risk_per_share`.
- **Risk-sized position sizing** — each entry risks `risk_per_trade_pct` of
  equity, further capped by per-symbol and portfolio exposure limits and the
  max concurrent open positions.
- **Drawdown circuit breaker** — trips and halts new entries when equity
  falls `max_drawdown_pct` below its high-water mark.
- **Daily loss guard** — flips the kill switch on if the day's P&L breaches
  `max_daily_loss_pct`.
- **Weekly loss / consecutive-loss guards** — halt new entries for the rest
  of the week if either limit is breached; both auto-reset on Monday.
- **Exits always run** — even while the kill switch is on (unless
  `exits_allowed_when_paused` is explicitly disabled), because a paused
  system must still be able to close open risk.
- **Stop-loss protection** — each cycle checks whether the session low
  breached any held position's recorded stop, and force-closes it if so.

## Broker adapters (`hf_trading_bot/broker/`)

- `PaperBroker` (default) — fully simulated fills, in-process. No
  credentials, no brokerage contact, cannot place a real order by
  construction.
- `AlpacaBroker` — **the recommended path.** Alpaca's official REST API,
  pointed at your *paper* account by default (real API, simulated money).
  Refuses to construct against any non-paper endpoint unless
  `HF_BOT_I_UNDERSTAND_LIVE_TRADING=true`.
- `RobinhoodBroker` — **dormant.** Live real money via the unofficial
  `robin_stocks` client. Kept for reference but not the supported route:
  it's a ToS-grey reverse-engineered client with fragile MFA/session
  handling. Alpaca does the same job through a documented API.

All three implement the same `Broker` interface
(`hf_trading_bot/broker/base.py`), so the strategy engine and risk logic
never change when you switch brokers.

## Market data (`hf_trading_bot/data/`)

Bars come from a pluggable provider, selected with `data_provider` in
`settings.yaml`:

| value | behaviour |
|---|---|
| `alpaca` (default) | Alpaca's official API, automatic yfinance fallback |
| `alpaca_only` | Alpaca only — fails loudly rather than falling back |
| `yfinance` | Yahoo scraping only (unofficial, breaks without notice) |

The fallback is deliberately **loud** (it logs a warning naming both sources)
and **all-or-nothing per call** — splicing Alpaca bars for one symbol with
Yahoo bars for another inside a single run would give a subtly inconsistent
view of the market.

**Free-tier caveat:** Alpaca's free plan serves the **IEX** feed, not the
full SIP consolidated tape. IEX is a subset of total volume, so daily bars
can differ slightly from TradingView or other charting sources. That's fine
for daily-bar swing strategies, but it explains any small discrepancies you
notice. Set `ALPACA_DATA_FEED=sip` if you have a paid data subscription.

## Alpaca setup

1. `cp .env.example .env` and fill in your keys from the Alpaca dashboard
   (Manage Accounts → your paper account → API Keys). The **Secret Key is
   shown only once** at generation — if you lose it, hit Regenerate.
   `.env` is gitignored. Never paste the secret into a chat or screenshot.
2. `cp config/settings.example.yaml config/settings.yaml` and set
   `broker: alpaca`.
3. Verify the connection:
   ```bash
   hf-bot alpaca-check
   ```
   Prints your account number, PAPER/LIVE mode, equity, whether the market is
   currently open, and any open positions. Unlike Robinhood there's no MFA
   step and no session file — Alpaca uses stateless API keys.
4. Dry run (reads balances/positions, places nothing):
   ```bash
   hf-bot run
   ```
5. Place simulated orders: `hf-bot kill-switch --off`, then
   `hf-bot run --live` or `hf-bot loop --live --interval 3600`.

### Guardrails

`--live` does not mean "unvalidated code can now trade." Before any order
goes out, `run --live` / `loop --live` check the watchlist's assigned
strategy against `hf-bot sweep-history`: if the most recent recorded sweep
for that strategy scored below a 50% hit rate against buy-and-hold, the
cycle refuses to run and explains why, citing the recorded numbers. All
five strategies shipped in this repo currently fail that check — see
[`FINDINGS.md`](FINDINGS.md). Reassign the watchlist to a strategy that
actually passed a sweep, or pass `--i-know-this-failed-backtest` to
override on purpose. A strategy that's never been swept isn't blocked —
this guards against redeploying a known-rejected result, not against
testing something new.

### Where to run the loop

On a machine you control that stays on — a home server, a VPS, or a laptop
that doesn't sleep. Don't run it in an ephemeral cloud container: when the
container is reclaimed the loop dies silently, and any stop-loss protection
dies with it (see *Fractional shares* below).

## Small-account constraints

### Pattern Day Trader (PDT) guard

US brokers restrict accounts under $25k to **3 day trades per rolling 5
business days** — a 4th gets the account flagged and restricted for 90 days.
The engine tracks this automatically (`storage.day_trades_in_window`) and
will **block a signal-based exit** that would close a position opened the
same day once the budget is spent, holding the position instead.

**Protective stop-loss exits always execute regardless of the budget.** If
price breaches a position's stop, the bot sells — a real loss is worse than
a PDT flag. This trade-off is logged explicitly whenever it happens.

Because of PDT, this bot is a swing-trading system (holds measured in days),
not an intraday day-trading system, on any account under $25k.

### Fractional shares

Orders are sized by dollar risk, not whole shares, so a small account can
take meaningful positions in high-priced symbols. Fractional orders are
**market-only** on both Alpaca and Robinhood — there's no broker-side stop
order for them. Stops are therefore enforced in software: the stop price is
recorded at entry and checked at the start of every cycle against the
session low. This means **a stop only triggers when the bot runs** — a gap
down while the process is stopped is not protected. Keep the loop running
during market hours, and size positions on the assumption that stops are
best-effort.

## Robinhood (dormant)

`RobinhoodBroker` still works and is covered by the same guards, but it is
**not the supported path**. It drives `robin_stocks`, an unofficial
reverse-engineered client: automating it is against Robinhood's terms, the
login needs an interactive MFA step whose session expires unpredictably, and
device/IP checks can force re-verification that an unattended process cannot
answer. Alpaca provides the same capability through a documented API with
stateless keys and a real paper environment.

If you do use it: `pip install -e ".[live]"`, set `ROBINHOOD_USERNAME` /
`ROBINHOOD_PASSWORD` / `HF_BOT_I_UNDERSTAND_LIVE_TRADING=true` in `.env`,
`broker: robinhood` in settings, then run `hf-bot robinhood-login` once
interactively. That saves a session to `~/.tokens/robinhood.pickle` —
**treat that file as a credential**; anyone holding it can trade your
account.

## Backtesting

```bash
hf-bot backtest QQQ --strategy rsi_mean_reversion --start 2021-01-01 --end 2026-01-01

# Test every strategy against a whole universe at once, and see if ANY of
# them beat buy-and-hold more often than a coin flip would.
hf-bot sweep --symbols AAPL,MSFT,NVDA,... --strategies momentum_90d,rsi_mean_reversion

# Review every past sweep verdict — a strategy that's already failed
# should stay failed, not get quietly re-tested until it looks good.
hf-bot sweep-history
```

Reports total trades, win rate, CAGR, Sharpe, and max drawdown from a
long-only, one-position-at-a-time, $10k-notional-per-trade replay
(`hf_trading_bot/backtest.py`), including a mark-to-market of any position
still open at the window's end (see [`FINDINGS.md`](FINDINGS.md) — dropping
that used to bias results against trend-following strategies). The output
names which data source served the bars, so a silent fallback can't be
mistaken for an Alpaca-backed result. `hf-bot sweep` additionally records
its verdict per strategy to the database, and `hf-bot run --live` /
`loop --live` refuse to trade a strategy whose most recent recorded sweep
scored below a 50% hit rate — see *Guardrails* below.

Always validate a strategy this way — and then in paper mode over real
time — before pointing it at any account holding real money.

## Portfolio tracking (`hf-bot portfolio`)

The only scorecard that can't be gamed by cherry-picking a strategy or a
window: given the money actually deposited, on the dates it was deposited,
is the account ahead of or behind simply buying the index with the same
cash?

```bash
hf-bot portfolio contribute --amount 500 --date 2026-01-05
hf-bot portfolio list
hf-bot portfolio compare --benchmark SPY
```

`compare` reads live positions from the configured broker (or takes
`--value` directly), buys fractional benchmark shares at each contribution
date's close, and reports the gap in dollars. This is what
`hf-bot journal scorecard` should ultimately be judged against, not
against "did the pick go up."

## Live Agent Cortex (`hf-bot dashboard`)

A dark HUD visualization of the 11-agent committee — each agent rendered as
a glowing particle cloud whose firing-rate number comes from real logged
data, with live readout panels (portfolio-vs-SPY gap, open theses, latest
sweep verdict, watchlist).

```bash
hf-bot dashboard                       # serve live at http://127.0.0.1:8420
hf-bot dashboard --port 9000 --refresh 30
hf-bot dashboard --publish cortex.html # one self-contained static file
```

Each agent carries a personal codename and a metric drawn straight from the
database — no fabricated numbers, matching the standard in
[`FINDINGS.md`](FINDINGS.md):

| codename | agent | firing rate = |
|---|---|---|
| **APEX** | cio | total decisions logged |
| **LATTICE** | portfolio-manager | SPY excess return |
| **BASTION** | risk-manager | % of BUY/SELL decisions risk-sized (stop + size set) |
| **ECHO** | behavioral-coach | discipline rate (rules followed on reviewed decisions) |
| **LEDGER** | equity-analyst | BUY theses logged |
| **CIPHER** | quant-analyst | latest sweep hit rate |
| **HORIZON** | macro-strategist | *no offline metric — shows NO SIGNAL* |
| **EMBER** | special-situations | high-conviction calls *(disclosed proxy)* |
| **RADAR** | setup-scanner | live-enabled watchlist size |
| **COMPASS** | valuation-analyst | avg embedded upside (target vs entry) |
| **TALON** | red-team | % of theses with a recorded objection |

Every number carries a `LIVE`, `PROXY`, or `NO SIGNAL` tag — the two agents
without a clean offline metric (HORIZON, EMBER) say so on the page rather
than inventing a figure. The particle motion is split into bright **core**
particles (scaled by the real metric) and dim **ambient** drift (decoration,
labeled as such) so animation is never mistaken for data.

Two delivery modes share one rendering engine (`hf_trading_bot/cortex.py`
builds the data, `cortex_render.py` draws it): the local server reads your
SQLite DB live and auto-refreshes; `--publish` bakes the data into a single
static HTML file with zero external requests, safe to host anywhere.

### Watching the committee work

The dashboard also shows the committee *interacting* — but only from real
logged events, never a decorative animation. When you run a review, the CIO
agent emits one event per stage (`hf-bot committee log-event`, wired into
`.claude/agents/cio.md`): a delegation, a specialist's finding, a gate's
verdict, the closing memo. The dashboard then pulses along the **actual**
handoff path, glows whoever is working right now, and — when the memo lands —
converges on APEX. When nothing is running it says **committee idle**, plainly.

```bash
# On your machine, a real review emits events as it runs:
Use the cio agent to run a full review on ASTS

# Or stage a realistic sample run to see the activity view immediately:
hf-bot committee demo            # a full, concluded review
hf-bot committee demo --partial  # left mid-review, an agent still working
hf-bot committee runs            # list recorded reviews
hf-bot committee show <run_id>   # replay one in the terminal
```

The **collective memory** panel is the honest version of "it learns": the
decision journal and committee log accumulate with every review, and
`scorecard()`'s discipline/accuracy make the team's calibration *measurable*
over time. It is a growing record, not a model that silently gets smarter —
the panel says so.

### Commanding the committee from the dashboard

The Stark-style HUD has a **command bar**: type a ticker (e.g. `ASTS`) and hit
DISPATCH to request a full committee review. The honest architecture matters
here — the Python server has no LLM, so it cannot run the agents itself:

- The command is **queued** (`command_queue`), never faked.
- A real **Claude session executes it**, emitting the committee events + memory
  that light up the cortex live.
- Two modes: by default the command **queues** for a Claude session to pick up
  (`hf-bot committee queue --run`); with `hf-bot dashboard --enable-agent-runner`
  the server itself spawns `claude -p` to run the review. The runner is **off by
  default** — a web page spawning Claude with your tools is a deliberate choice,
  not a default.

Only a validated ticker ever reaches an executor, so no free-form text can be
smuggled into a spawned process. The "Command deck" panel tracks each request's
status (pending → running → done).

### The team learns: a growing, role-specific library

Each agent builds a curated body of expertise it **recalls at task time**, so
the committee gets sharper the more it studies. The honest mechanics (this is
learning-by-accumulation, not a retrained model) live in
[`knowledge/README.md`](knowledge/README.md).

```bash
hf-bot study status                       # per-agent library size (coverage bars)
hf-bot study next --agent red-team        # next curriculum topic + a study brief
hf-bot study cycle --rounds 3             # briefs for the 3 least-studied agents
# an executor researches, writes knowledge/<agent>/<slug>.md, then:
hf-bot study record --agent red-team --topic famous-blowups --slug famous-blowups --sources 3
```

- **Curriculum:** `knowledge/curriculum.yaml` — per-agent topics (value
  investing for LEDGER, monetary history for HORIZON, famous blow-ups for
  TALON, …) with why-it-matters and research anchors.
- **Library:** `knowledge/<agent>/<slug>.md` — distilled, sourced notes,
  git-committed so the knowledge is durable (the memory *database* is
  gitignored; these files are the source of truth). See
  `knowledge/red-team/famous-blowups.md` for the format.
- **Recall:** every specialist reads its `knowledge/<agent>/` dir and runs
  `hf-bot memory recall` before any task — that's what turns a growing library
  into sharper operators.
- **Bootstrap then trickle:** `Use the cio agent to run a study cycle` (large
  `--rounds` to seed the whole team, then `--rounds 1–2` ongoing).

**Optional autonomous learning (off by default).** To have the team study on a
schedule without you, a Claude Code Routine can fire a study cycle — e.g. daily
— and commit the new notes. It spends tokens on its own, so it's opt-in: enable
it deliberately (a `create_trigger` firing `Use the cio agent to run a study
cycle --rounds 2` in this repo's environment), and disable it any time. Nothing
runs autonomously unless you turn it on.

### Shared memory that persists across sessions

Beyond the counts, the committee keeps a **durable, recallable memory** — the
distilled conclusion of each review, carried forward so the next one builds
on it instead of starting cold.

```bash
hf-bot memory recall ASTS          # search shared memory before a review
hf-bot memory persist --kind decision --symbol ASTS \
  --title "ASTS: BUY sized small" --body "<self-contained, dated>"
hf-bot memory list                 # everything the committee has learned
```

Two layers, by design:

- **Local ledger (source of truth).** Episodes live in the repo's SQLite, so
  the memory survives across sessions with *no external dependency*. The
  dashboard reads this — shown as `◇`.
- **Agently brain (cross-session amplifier).** The CIO agent also mirrors each
  episode into the [Agently](https://agent.ly) knowledge graph, a memory
  shared across sessions and other AI tools. Once mirrored, the dashboard
  marks it `◈`. If that service is unreachable (e.g. out of credits), nothing
  is lost — the local ledger already holds it, and the mirror happens later.

The recall-first / persist-last protocol is wired into `.claude/agents/cio.md`,
so a real `Use the cio agent to review X` run consults past conclusions on the
way in and deposits a new one on the way out.

## Tests

```bash
pytest
```

Covers the risk math (circuit breaker, position sizing, weekly guards),
each strategy's entry/exit logic on synthetic price series, the backtest
replay/stats engine (including the open-position mark-to-market fix and
the live-trading guard), the sweep-result persistence, and the portfolio
contribution/counterfactual math. All offline — no network calls.

## What's intentionally not here

- No options/futures/forex/multi-broker support yet — the `Broker`
  interface is built to add adapters, but only equities via Robinhood/paper
  are wired up.
- No resting bracket/trailing-stop orders at the broker — stops are
  recorded at entry and enforced by the bot's own per-cycle check, not by
  broker-side OCO orders (Robinhood's API doesn't expose full bracket
  orders the way Alpaca's does, and fractional orders are market-only).
  See the stop-loss caveat under "Fractional shares" above.
- No intraday/day-trading engine — daily bars only, which is also what the
  PDT rule effectively forces on accounts under $25k.
- No holiday calendar in the PDT window (weekend-skipping only), so the
  5-business-day window can be slightly conservative around market holidays.
- No guarantee of profitability, ever.
