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
hf-bot dashboard --open                # serve live and open your browser
hf-bot dashboard --host 0.0.0.0 --open # + reachable from your phone on the same wifi
hf-bot dashboard --port 9000 --refresh 30
hf-bot dashboard --publish cortex.html # one self-contained static file
```

On Windows, double-click **`run-dashboard.bat`** (token-free HUD) or
**`run-agents.bat`** (full agent mode + tiered models) — it starts the server
and opens your browser.

### Two ways the desk acts: investment review vs. tactical trade

The committee runs **two distinct processes** — don't confuse them:

- **Investment review** — type a ticker or "should I buy X" in the console.
  The full long-horizon pipeline runs: regime → research → valuation → red-team
  → risk → behavioural. The index is the hurdle and **PASS is a good outcome**;
  most ideas are passed on. This machine is *designed* to be cautious.
- **Tactical trade — SNIPER-led** — type **`trade BTC`**, **`snipe AAPL`**, or
  **`tactical TSLA`**. SNIPER reads the tape and *leads*: it delivers
  entry/stop/target, the risk-manager sizes from the stop, the red-team runs a
  fast veto, and VECTOR **stages a paper order** in the Orders panel for your
  approval. This path does **not** run valuation/opportunity-cost and does
  **not** default to PASS — the desk acts when the tape supports a clean setup.
  Works for **equities and crypto** alike.

Nothing is ever placed automatically: a tactical trade *stages* a proposal that
you approve or reject in the Orders panel — paper money only, behind the kill
switch.

### Crypto

Alpaca trades crypto and equities through the same account. Write a pair as
`BTC/USD` (or a bare base like `BTC` — it's expanded to `BTC/USD`). Crypto is
handled end-to-end: its own market-data endpoint, `time_in_force=gtc` orders
(Alpaca rejects `day` for crypto), it's **PDT-exempt** (not a security) and
trades 24/7. Put pairs in your `watchlist` (see `config/settings.example.yaml`),
chart them with `hf-bot chart BTC/USD`, or trade one with `trade BTC` in the
console.

### Memecoin dashboard panel — view, command, and (opt-in) fully autonomous

Everything above also lives in `hf-bot dashboard` once `SOLANA_PRIVATE_KEY` is
set — no separate app. A **Memecoin** panel shows your wallet, budget used,
live positions with unrealized P/L, recent activity, and (when armed)
autotrade status. A separate command bar next to it takes:
```
screen                    # candidates that pass entry criteria right now
check <mint>              # the rug-risk screen for one token
buy <mint> <usd>          # a real buy — the typed command is the confirmation
sell <mint> <pct>         # a real sell, same as above
```

**Fully autonomous mode** — launch with `--memecoin-autotrade` and the bot
runs the whole playbook above with **no approval click**. This is the
biggest step in the whole feature — say so plainly: **real money moves
without you watching.** It only starts if `HF_BOT_I_UNDERSTAND_MEMECOIN_RISK=true`
and `SOLANA_PRIVATE_KEY` are both configured; otherwise the dashboard prints
a loud `BLOCKED` banner and runs everything else normally. The kill switch,
per-trade ceiling, and wallet budget all still apply every cycle — nothing
about autonomy raises those caps.

Entries and exits run on **two independent loops, deliberately different
speeds**:
- **Exit checks** (`--memecoin-exit-check-seconds`, default 10s) — every
  held position against its stop-loss/trim/trailing-stop rule. This only
  touches your own handful of open positions, which costs nothing on any
  rate limit, so there's no reason a stop-loss should wait on the slower
  entry-scan cadence to fire.
- **Entry scanning** (`--memecoin-cycle-seconds`, default 300 = 5 min) —
  screening new candidates. This one genuinely has to stay slower: it calls
  RugCheck and pump.fun's free-tier APIs per candidate, and scanning
  dozens of coins every few seconds would blow through those limits fast.

```bash
hf-bot dashboard --open --memecoin-autotrade                       # entries every 5 min, exits every 10s
hf-bot dashboard --open --memecoin-autotrade --memecoin-cycle-seconds 900 \
                  --memecoin-exit-check-seconds 5                  # slower entries, faster exits
```

Without `--memecoin-autotrade`, the dashboard still shows the wallet and
positions (refreshed on the same interval) — you get the view and the
command bar, and trigger a cycle manually with the **RUN CYCLE NOW** button.

### Scalp mode — brand-new pump.fun coins, tight exits (`--memecoin-scalp`)

`--memecoin-autotrade` on its own runs the swing profile above: DexScreener's
trending list, +100%/+300% take-profit trims, a 6h stall window. Add
**`--memecoin-scalp`** and both halves switch to a different, deliberately
riskier profile:

- **Discovery** comes from **pump.fun's own new-coin feed** (`pumpfun_data.py`)
  instead of DexScreener — coins seconds/minutes old, the way Photon's
  Memescope shows them, not the trending-boosts list. DexScreener only
  indexes a pair once it has a liquidity pool, so it structurally cannot show
  something this new.
- **This is an unofficial, undocumented API.** It can change or break
  without notice — test it first: `hf-bot memecoin newcoins` (or `--raw` to
  see the raw response if something looks wrong).
- **No liquidity-based safety check applies.** A bonding-curve coin this
  fresh has no comparable liquidity figure the way a DexScreener pair does —
  only **mint/freeze authority** is checked. That is a real, deliberate
  increase in risk, not a smaller version of the normal screen; it is what
  trading a coin this early means.
- **Exits are tight:** stop-loss **-15%** (vs -35%), first trim at **+15%**
  sells **75%** of the position (vs +100%/50%), trailing stop after +20%,
  and a **30-minute** stall exit (vs 6h) — if it hasn't moved by then, out
  regardless.
- **The first trim sells most of the position, not half.** Live testing
  produced a trade that trimmed 50% at +20.3%, let the other half ride, and
  later stopped it out at -31.3% from entry — blended, roughly a **-5% net
  loss** despite two "successful" green exits in the log. The trailing stop
  (activates at +20%, exits on a 15% pullback from peak) should have
  protected most of that remaining half, but the same periodic-checking gap
  documented below for stop-loss applies to it too: a big enough move
  between one 10-second check and the next can jump straight past the
  trailing zone. Trimming 75% up front instead of 50% means less stays
  exposed to that gap — the tradeoff is giving up more upside on a coin
  that keeps running after the trim.
- **The -15% stop-loss is a check-and-react rule, not a hard limit.** Live
  testing produced a real, sobering example: a position hit the exit-check
  loop already down -66.4% — the price gapped down between one 10-second
  check and the next, which a genuinely fast-crashing pump.fun coin can do
  easily. There is no on-chain stop-limit order type here; the bot can only
  react to what it observes at each check, and the realized loss on a real
  crash can be dramatically worse than the configured percentage implies.
  A related failure compounded it: the sell itself was initially rejected
  twice by pump.fun's own program (a slippage-tolerance failure) at the
  normal 150bps tolerance, because the price was moving faster than that
  between quote and execution. Exits triggered by stop-loss or trailing-stop
  now use a much wider tolerance (`EMERGENCY_EXIT_SLIPPAGE_BPS`, 2500bps =
  25%) specifically — getting out at a worse price beats repeatedly failing
  to get out while the position keeps bleeding value. Take-profit trims
  keep the normal tolerance; they're not emergencies.
- **The same thing happens on the way IN, not just the way out.** Live
  testing hit `custom program error: 0x1771` (Anchor error 6001, the same
  slippage-tolerance code as the sell-side failure above) on a scalp-mode
  BUY, at the 100bps default meant for an established DexScreener pair. A
  brand-new pump.fun coin trades directly against its bonding curve, not a
  deep pool — the price moves far more than 1% between quote and landing
  when it's seconds old. Scalp-mode entries now use `SCALP_BUY_SLIPPAGE_BPS`
  (1000bps = 10%) instead of the normal `DEFAULT_BUY_SLIPPAGE_BPS` (100bps);
  non-scalp entries (established pairs via DexScreener) keep the tighter
  default. Both are overridable via `MEMECOIN_BUY_SLIPPAGE_BPS` /
  `MEMECOIN_SCALP_BUY_SLIPPAGE_BPS` if 0x1771 still shows up on buys.
- **And the same thing on a routine exit, not just a crash.** Live testing
  hit 0x1771 a third time — on an ordinary take-profit trim, no stop-loss
  involved, at the normal 150bps exit tolerance. The raw error showed the
  inner pump.fun `SellV2` call actually succeeding; it's Jupiter's own
  `Route` instruction enforcing its slippage-derived minimum-out at the
  very end that rejected the whole simulated transaction. The position
  traded fine on the next exit-check pass 10 seconds later (a fresh quote
  cleared it), but 150bps — sized for an established pair — turned out too
  tight for ANY pump.fun bonding-curve exit, not only an emergency one.
  Non-emergency scalp-mode exits now use `SCALP_SELL_SLIPPAGE_BPS` (500bps
  = 5%): wider than the 150bps normal default, narrower than the 2500bps
  emergency ceiling. A stop-loss or trailing-stop still always gets the
  emergency tolerance regardless of mode — urgency wins over everything.
  Configurable via `MEMECOIN_SELL_SLIPPAGE_BPS` /
  `MEMECOIN_SCALP_SELL_SLIPPAGE_BPS` / `MEMECOIN_EMERGENCY_EXIT_SLIPPAGE_BPS`.

```bash
hf-bot memecoin newcoins                                   # test the feed first
hf-bot dashboard --open --memecoin-autotrade --memecoin-scalp --memecoin-cycle-seconds 90
```

A short cycle actually matters for scalping — **the free public Solana RPC
will rate-limit at 60-120s cycles**; a paid RPC (Helius, QuickNode) is
recommended for scalp mode specifically. Everything else stays the same: the
kill switch, `MEMECOIN_MAX_TRADE_USD`, and `MEMECOIN_WALLET_BUDGET_USD` all
still apply exactly as before — scalp mode changes *what* gets bought and
*when* it gets sold, never *how much* the bot is allowed to risk in total.

### Live feed — real-time detection, not polling (`--memecoin-live`)

`hf-bot memecoin newcoins` (and scalp mode without `--memecoin-live`) polls
pump.fun's REST API once per cycle — good enough for testing, but it only
ever sees "whatever was new the last time it happened to ask." A real live
feed means a **persistent WebSocket subscription** that's told the instant a
token is created on-chain — the actual mechanism Photon's Memescope-speed
bots use. `--memecoin-live` is that: it subscribes to Solana program logs
mentioning the pump.fun program, and for every matching transaction looks
for a brand-new SPL token mint appearing in that transaction's token-balance
change — sub-second detection, not a polling interval.

```bash
pip install -e ".[memecoin]"          # now also pulls solana + websockets
hf-bot memecoin watch                 # verify the connection works — watch for a minute
hf-bot dashboard --open --memecoin-autotrade --memecoin-scalp --memecoin-live \
                  --memecoin-cycle-seconds 90
```

**Always run `hf-bot memecoin watch` first.** It connects to the real feed
and prints each detection as it happens, with your eyes on the output —
exactly the same verification step as `newcoins --raw` for the REST path,
just for the thing that's actually going to feed the autonomous scalp loop.

Two things stated plainly:
- **The detection heuristic is a new mint appearing in a transaction that
  touches the pump.fun program** — inferred from stable, documented Solana
  RPC semantics (pre/post token balances), not by parsing pump.fun's own
  undocumented log format. It should catch essentially every pump.fun token
  creation; a rare false positive (something else creating a token in a
  transaction that also happens to touch the program) is possible.
- **This has never been exercised against a live connection while building
  it** — every RPC/websocket call was verified against the installed
  library's actual method signatures, but this development environment
  cannot reach Solana's network at all. `hf-bot memecoin watch` is not
  optional the first time — it's the only way either of us finds out if
  something needs fixing before real money is involved.

`--memecoin-live` requires both `--memecoin-autotrade` and `--memecoin-scalp`
— it's ignored (with a banner note explaining why) otherwise. The dashboard's
Memecoin panel shows the feed's live connection state and detection count.

### Buyer diversity and rug screening — PumpPortal + RugCheck

The scalp entry score originally used only freshness + raw SOL raised on the
bonding curve. Those two work against each other (a coin needs to be very
fresh AND already have real SOL raised, which takes time freshness is
spending down), and worse, SOL raised fast from ONE wallet — the textbook
bundled/insider-launch pattern — scored identically to genuine broad-based
buying. Two free integrations fix that:

- **PumpPortal** (`pumpportal.fun`) — a free, keyless third-party WebSocket
  feed watching the same on-chain pump.fun events. It supplies *distinct
  buyer count* per candidate mint, which the momentum score now weighs as
  heavily as freshness (40/40/20 split with SOL-raised). It runs automatically
  whenever `--memecoin-scalp` is set — no extra flag needed. Verify it works
  on your machine first, same reasoning as the live feed above:
  ```bash
  hf-bot memecoin pp-watch --seconds 120
  ```
  This is a supplementary signal only — if PumpPortal is unreachable or a
  mint has no buyer data yet, scoring just falls back to freshness +
  SOL-raised, never blocking a trade on a missing connection.

- **RugCheck.xyz** — a free, keyless-for-reads REST API giving top-holder
  concentration and a composite risk score. `memecoin.rugcheck_flags()` calls
  it as a **final gate right before a buy executes** (not for every scanned
  candidate, to stay well under the free-tier rate limit) — a red flag there
  vetoes the buy even after the momentum score already cleared. This is the
  one check that can actually see a bundled/insider launch; the on-chain
  mint/freeze-authority check alone can't.
  A single-holder check alone still misses one pattern: a bundle
  deliberately split across several wallets, each individually under the
  20% red threshold, collectively holding a large share of supply — the
  same insider pattern, just spread thin enough to dodge a single-wallet
  check. `rugcheck_flags()` now also flags red when the **top 5 non-pool
  wallets combined** hold ≥35% (`RUGCHECK_TOP5_HOLDER_RED_PCT`) — this costs
  no extra API calls, since RugCheck's report already returns the full
  holder list; it was previously discarded down to a single number.

Both are genuinely free and neither requires an API key to function (an
optional `RUGCHECK_API_KEY` just raises RugCheck's rate limit). Like the live
feed, PumpPortal's exact message schema is implemented from public docs, not
verified against a live connection from this dev environment — `pp-watch` is
the honest way to find out if that's held up before it feeds real scoring.

**A real tuning lesson from live testing:** the first live run showed real
scalp candidates consistently coming back `pumpportal: no record for this
mint`. The cause wasn't a parsing bug — a follow-up `pp-watch` run showed
pump.fun creating a new coin roughly every 1-2 seconds even in a quiet
moment, far faster than the original 50-mint/10-minute tracking window could
hold. `PUMPPORTAL_MAX_WATCHED_MINTS` (default 300) and `PUMPPORTAL_WATCH_TTL_S`
(default 180) exist so this can be tuned to whatever volume you actually
see — if `pp-watch` still shows lots of "no record" for real candidates,
raise the cap or shorten the TTL further. Even after fixing that and a
likely subscription bug (each new mint's subscribeTokenTrade call now
resends the FULL watch list, not just the newest mint — see the module
docstring), live buyer counts stayed stubbornly at zero across many tracked
mints, which real pump.fun trading volume makes implausible as "just quiet
coins."

**The pivot that actually worked in testing:** live buyer diversity never
got confirmed working end-to-end. What *did* work — validated by directly
comparing against Photon's own Memescope, filtered to its "$10k+ market
cap" preset — is market cap itself: coins crossing that threshold in their
first minute consistently showed dozens to hundreds of real holders, a
single, robust number instead of many individually-fragile trade events.
`pumpfun_momentum_score` now weighs freshness(25) + market cap(35) + buyer
diversity(25) + SOL-raised(15); market cap reaches full score at
`MARKET_CAP_FULL_SCORE_USD` ($10,000, matching that Photon setting exactly).
`run_autotrade_cycle` fetches a live market cap per scalp candidate
(`pumpfun_data.get_coin()`) when the live feed's own bare-mint-address
detection doesn't already carry one. Buyer diversity stays in the mix at a
reduced weight rather than being ripped out — it may yet prove itself once
the subscription fix has more live runtime, but market cap is the signal
this project actually has evidence for.

**Deliberately not implemented: following social media for new coin drops.**
Coordinated Twitter/Telegram hype is the mechanism a pump-and-dump
manufactures fake demand with in the first place — a bot that chases
"trending" coins gets more exploitable, not more informed, and meaningful
social-platform API access isn't free either. What's used instead: a flat
`SOCIAL_LINKS_BONUS` (5 points) for whether the creator attached ANY
social/website link at pump.fun creation — not what it says, just whether
it exists. Free (same `get_coin()` fetch that already retrieves market
cap), hard to fake, and deliberately small — a tie-breaker, not a driver
of entries.

### Why entries are rare, and the real detection-coverage fix

After all the above landed, live testing still showed very few real
candidates — sometimes 20+ "no" results in a row with nothing close to
passing, while the same coins were findable by eye on Photon's Memescope
within seconds. Two things turned out to be true at once:

1. **Most pump.fun coins genuinely are worthless** — one widely-cited
   estimate puts ~98% of pump.fun tokens as rug pulls, bundles, or
   abandoned within minutes. Seeing mostly rejections is largely correct
   behavior, not a bug.
2. **But `pumpfun_live.py`'s RPC feed was also missing most real new
   coins**, for a concrete reason: it subscribes to every transaction
   mentioning the pump.fun program — creates, buys, AND sells — but can
   only afford ~3 `getTransaction` calls/sec (`PUMPFUN_LIVE_MIN_TX_INTERVAL_S`)
   to avoid tripping Helius's rate limit. Given pump.fun's real volume,
   that budget is mostly consumed by unrelated buy/sell traffic, and
   creates are a small fraction of even that — so the RPC feed likely
   sees only a small, effectively random slice of actual new coins.

PumpPortal's `subscribeNewToken` stream gets every creation event directly,
with no rate limit and no follow-up RPC call needed (PumpPortal pushes it).
`pumpportal_live.py`'s `recent_new_coins()` now feeds these into
`run_autotrade_cycle` and the dashboard's `screen` command, **merged**
alongside `pumpfun_live.py`'s detections (deduplicated by address,
re-sorted newest-first) — not replacing that feed, since it's the one
verified end-to-end against a real connection. This closes the coverage
gap without discarding what's already proven to work. Symbol/name are
captured directly from PumpPortal's create event; market cap/price/social
links still come from the same `pumpfun_data.get_coin()` enrichment fetch
already built, rather than guessing at PumpPortal's numeric field
semantics on top of an already-unverified message schema.

`hf-bot memecoin pp-watch` now shows both new-coin detections and
buyer-diversity counts, so this is verifiable the same way as everything
else here — with real output, before trusting it inside the autonomous loop.

**Two follow-on fixes from the same live-testing round, worth noting
separately:**

- **PumpPortal-speed race on mint checks.** Merging in PumpPortal's feed
  caused a wave of `entry-mint-check: no on-chain account for mint ...`
  errors — PumpPortal notifies of a new mint essentially the instant the
  transaction is broadcast, sometimes faster than our own RPC node has
  confirmed it. `_get_mint_info_for_fresh_candidate()` retries that
  specific failure (0.5s, then 1s) for scalp candidates only; any other
  `WalletError` still fails immediately, no blind retry.
- **Parallel market-cap enrichment.** With PumpPortal merged in, a single
  entry cycle can carry dozens of candidates missing a market cap —
  fetching those one at a time inside the scoring loop meant a real chunk
  of each cycle was spent waiting on sequential REST calls rather than
  actually deciding anything. `enrich_candidates_with_market_cap()` fetches
  all of them up front with bounded concurrency (5 workers by default —
  not unlimited, since blasting pump.fun's free-tier API at once would
  likely trip its own rate limit) before the scoring loop runs at all.
- **Safe retry on a Jupiter network blip** (`_with_jupiter_retry()`).
  `jupiter.py`'s `quote()`/`swap_transaction()` (the only calls that can
  raise `JupiterError`) both happen strictly before anything is signed or
  broadcast, so a `JupiterError` means no money has moved yet — retrying is
  safe from a double-spend, unlike `WalletError` (which can occur *after*
  submission, e.g. a timeout waiting for confirmation, where blindly
  retrying could buy/sell twice). Used by `execute_buy`/`execute_sell`
  calls in `run_autotrade_cycle`, `run_exit_check`, and `multi_buy`. Still
  worth having even after the real cause below was found — an actual
  transient blip can still happen.
- **The real cause of the persistent "Jupiter unreachable: [Errno 11001]
  getaddrinfo failed"**: not a network blip, not a retry-window problem —
  `quote-api.jup.ag/v6` (the endpoint this bot originally used) was
  **deprecated by Jupiter and had its DNS records fully removed**. No
  retry window, however wide, was ever going to fix a hostname that no
  longer resolves anywhere. A plain `nslookup quote-api.jup.ag` outside
  the bot confirmed it before this was diagnosed as a domain migration
  rather than a router/DNS-relay issue. Fixed by updating
  `DEFAULT_BASE_URL` to the current `api.jup.ag/swap/v1` — the query
  params, request body, and response field names (`outAmount`,
  `priceImpactPct`, `swapTransaction`) are unchanged, so this was a
  one-line fix once identified. Override via `JUPITER_BASE_URL` in `.env`
  if Jupiter migrates domains again.
- **Jupiter now requires an API key, even for free use.** Live testing
  produced `entry-buy: Jupiter quote HTTP 429: {"code":429,"message":"[API
  Gateway] Too many requests"}` almost immediately after the domain fix
  above — a different failure from the DNS issue, and this time not a
  bug in this bot at all. Jupiter's own docs confirm an unauthenticated
  request to `api.jup.ag` is capped at roughly **0.5 requests/second**; a
  single autotrade cycle (one quote per scanned candidate, plus exit
  checks every 10s) exceeds that trivially. Fixed by adding `JUPITER_API_KEY`
  support — set it and every quote/swap call sends it as the `x-api-key`
  header Jupiter expects. Get a free key at portal.jup.ag (no cost); a
  free-tier key's limit is well above the keyless 0.5 RPS cap.
- **Mint-check rate limiting.** The same live-testing round that surfaced
  more real candidates than ever also tripped a NEW problem: "Solana RPC
  HTTP 429: Too Many Requests" on `entry-mint-check`. With PumpPortal
  merged in, a single cycle can put dozens of candidates through a
  getAccountInfo call, back to back, with no spacing — the exact class of
  problem `pumpfun_live.py`'s `getTransaction` throttle already exists to
  avoid, just on a different RPC call. `_rate_limited_get_mint_info()`
  applies the same fix here (`MEMECOIN_MINT_CHECK_MIN_INTERVAL_S`, default
  0.35s ~= 3/sec, same default as the live feed's throttle) — raise it if
  429s persist on your RPC plan.
- **The SAME 429 later showed up on `positions`, not just entries.**
  `list_positions()` calls `get_token_balance()` once per distinct token
  ever traded — every single 10-second exit-check tick, forever, completely
  unthrottled. Two independently-throttled call types can each individually
  stay under ~3/sec and still blow past the RPC provider's real combined
  limit, since both hit the same endpoint. `_rate_limited_get_mint_info()`
  is now `_rate_limited_solana_call()`, a generic wrapper both mint-info
  checks AND position balance checks route through — one shared clock,
  one shared budget, `MEMECOIN_MINT_CHECK_MIN_INTERVAL_S` still controls it.
  Worth knowing: this call is still one per token *ever* traded, not just
  currently held ones, so a wallet with a long trade history pays a fixed
  per-cycle cost that grows with that history — not a problem at normal
  scale, but a free public RPC that's already 429ing on everything else is
  a sign it's time for a paid one (Helius, QuickNode), not more throttling.

### Memecoin trading playbook — entry/exit criteria (`memecoin screen` / `positions`)

Two commands turn "what to look out for" into concrete, checkable rules —
built entirely from free data already in use (DexScreener's momentum fields,
Solana's own mint-account state). No social/Twitter signal, no paid data.

**What to look out for (the risk screen, `memecoin check`):**
- Mint or freeze authority not revoked — the creator can mint unlimited
  supply, or freeze your wallet's tokens outright. Either is disqualifying.
- Liquidity under $5k (red) or under $20k (yellow) — thin liquidity means
  high slippage and possibly no way to sell back out.
- 24h volume more than ~20x liquidity — often wash trading, not real demand.
- A pool under 24h old — unproven.

**What signifies good momentum (`memecoin screen`):** a 0-100 score from —
- **Acceleration**, not just size: the 1h price move outpacing the 6h
  average pace means fresh buying, not an old move you're late to.
- **Buy/sell pressure**: the ratio of buys to sells in the last hour (needs
  at least 10 transactions to be readable at all).
- **Liquidity depth** and a **volume/liquidity ratio in a healthy range**
  (active, not wash-trading-shaped).

A token only shows as a `screen` PASS if it has **zero red risk flags** and
its momentum score clears the threshold (default 60/100). Run it:
```bash
hf-bot memecoin screen                    # trending tokens that pass entry criteria
hf-bot memecoin screen --show-all         # see every candidate and why each did/didn't pass
```

**When to enter:** only on a `screen` PASS, and only after independently
confirming with `memecoin check` (the full risk detail) and `memecoin quote`
(actual price impact for your size) — `screen` is a filter, not an
instruction.

**When to exit (`memecoin positions`) — checked in this order:**
1. **Stop-loss: -35% from entry.** Wider than an equity stop on purpose —
   memecoins routinely swing 20-30% intraday with no signal in it; a tighter
   stop would exit on noise. Capital protection always outranks the rest.
2. **Take-profit trim #1 at +100%:** sell half, de-risk the trade.
3. **Take-profit trim #2 at +300%:** sell half of what's left.
4. **Trailing stop:** once a position has been up 50%+ at its peak, exit the
   remainder if price gives back 30% from that peak — protects gains already
   made without capping the upside before then.
5. **Momentum-stall exit:** held 6h+ with the 1h move negative and sell
   pressure exceeding buy pressure — the move this was betting on is over.

```bash
hf-bot memecoin positions   # live holdings + a suggested action for each, with why
```
`positions` is what tracks the peak price and updates it every time you run
the command — **the trailing stop is only as accurate as how often you check
it.** Nothing here executes automatically; every suggested action is
something you then run `memecoin sell` for yourself. Pass `--mark-trim 1` or
`--mark-trim 2` on a `sell` matching a take-profit trim so `positions` doesn't
keep recommending the same trim again; selling 100% clears all tracked state
for that token automatically.

**The honest limit of all of this:** every signal above describes what a
token has *just* done. None of it predicts what happens next, and momentum
reverses without warning. This tooling exists to make the mechanical part —
catching the same rug pattern twice, forgetting a stop-loss, holding a dead
token out of hope — disciplined and automatic to *check*. It does not, and
cannot, tell you which token wins.

### Memecoin trading (pump.fun / Solana)

A separate, opt-in feature for trading Solana memecoins (pump.fun-style
launches) via the Jupiter aggregator. **Read this whole section before using
it — it is categorically different from everything else in this bot.**

**Why it's different.** Alpaca is a regulated broker with a real paper-trading
mode. Solana has no such thing: every trade is a **real, irreversible
on-chain transaction** signed with a **real private key**, funded with **real
money**. There is no kill switch for a transaction already broadcast. Most
tokens on this market have no fundamentals and a large share are explicitly
designed to be dumped on early buyers — treat any balance you put here as
money you're comfortable losing entirely.

**It is never automatic.** No agent, no `--auto-execute` sweep, no scheduled
study or Routine can ever reach this code — every buy/sell is a command you
typed yourself.

**Setup:**
```bash
pip install -e ".[memecoin]"
```
Use a **wallet dedicated to this bot** — not your main Phantom wallet. In
Phantom: add a new account, send it only the amount you're funding the bot
with, then export *that* account's private key (Settings → the new account →
Export Private Key) into `.env` as `SOLANA_PRIVATE_KEY`. That way this code
never has custody of anything else you hold. See `.env.example` for every
variable (`SOLANA_PRIVATE_KEY`, `SOLANA_RPC_URL`, `MEMECOIN_MAX_TRADE_USD`,
`MEMECOIN_WALLET_BUDGET_USD`, `HF_BOT_I_UNDERSTAND_MEMECOIN_RISK`).

**Guards, all of which must pass or the trade is refused before anything is
signed:**
- `HF_BOT_I_UNDERSTAND_MEMECOIN_RISK=true` — a deliberate, typo-proof opt-in.
- The kill switch (the same one Alpaca trading respects).
- A per-trade ceiling, `MEMECOIN_MAX_TRADE_USD`.
- A cumulative wallet budget, `MEMECOIN_WALLET_BUDGET_USD` — net USD deployed
  (buys minus sells) can never exceed it.
- A live SOL balance check, `MEMECOIN_MIN_SOL_RESERVE` (default 0.02 SOL).

**Trade size scales with conviction, not a flat amount.** Every autotrade
candidate that clears the entry gate already has an entry-signal score (the
momentum/market-cap/buyer-diversity composite `entry_signal()` or
`pumpfun_entry_signal()` computes) — that score used to be a pure pass/fail
gate and nothing else, so a candidate that barely cleared the threshold got
exactly the same dollar size as one that scored near-perfect. Sizing now
scales linearly between `MEMECOIN_MIN_TRADE_USD` (a candidate right at the
entry threshold) and `MEMECOIN_MAX_TRADE_USD` (a perfect 100 score) —
`memecoin.size_for_score()`. Two things this does **not** do: it never lets
a candidate in that would otherwise fail the entry/RugCheck gates (scaling
only decides *how much*, never *whether*), and it never raises exposure
above the existing `MEMECOIN_MAX_TRADE_USD` ceiling. Scalp-mode scores in
particular are built from very thin, seconds-old data, so treat this as
sizing conservatism, not as the bot getting more confident than the
underlying signal actually warrants.

**Why the live balance check exists.** Live testing produced a real
`entry-buy: Solana RPC error ... custom program error: 0x1 ...
'Transfer: insufficient lamports 236352768, need 260603976'` — the wallet's
actual on-chain SOL was thinner than what `MEMECOIN_MAX_TRADE_USD` and
`MEMECOIN_WALLET_BUDGET_USD` assumed was available. Those two caps are a
**virtual USD ledger** (buys minus sells recorded in the local database),
not a live wallet balance — real SOL also drains for transaction fees and
new-associated-token-account rent on every trade, none of which that ledger
tracks. Every buy now calls the wallet's real balance right before signing
and refuses cleanly (`MemecoinError: insufficient SOL: ...`) if what would
be left over after the trade dips under the reserve — instead of building
and submitting a transaction that Solana's own simulation was always going
to reject. Raise `MEMECOIN_MIN_SOL_RESERVE` if you still see raw
"insufficient lamports" errors from the chain (meaning the default reserve
didn't cover your RPC's actual fee/rent cost).

**Be clear about what these do and don't protect:** they bound what *this
code* will voluntarily spend. They do **not** limit what the raw private key
is capable of if it's ever exposed — the key controls the whole wallet it's
in, which is exactly why it should be a dedicated wallet with only your
intended budget in it.

```bash
hf-bot memecoin wallet                          # address, SOL balance, budget used
hf-bot memecoin scan                             # trending Solana tokens (data only)
hf-bot memecoin scan --query BONK                # search for a specific token
hf-bot memecoin check --token <MINT>             # mechanical rug-risk screen — read-only
hf-bot memecoin quote --token <MINT> --usd 10    # preview a buy — spends nothing
hf-bot memecoin buy --token <MINT> --usd 10 --dry-run   # full preview, still nothing sent
hf-bot memecoin buy --token <MINT> --usd 10      # real trade
hf-bot memecoin sell --token <MINT> --pct 100    # sell all of a held position
hf-bot memecoin history                          # every trade this bot has made
hf-bot memecoin multi-buy --count 3 --usd-each 10          # spread across 3 trending tokens
hf-bot memecoin multi-buy --usd-each 10 --tokens <M1>,<M2> # or name them explicitly
hf-bot memecoin positions                        # live holdings + unrealized P/L
```

**On `memecoin check` — what it can and can't tell you.** `scan` lists tokens
by *paid promotion*, not quality — attention, not a recommendation. `check`
is the closer thing to real screening: it reads live, mechanical facts and
flags the most common instant-rug patterns —
- **mint/freeze authority not revoked** (red) — the creator can mint
  unlimited new supply, or freeze your wallet's tokens outright;
- **thin liquidity** (red under $5k, yellow under $20k) — high slippage,
  possibly unsellable;
- **volume far exceeding liquidity** (yellow) — often wash trading, not
  organic demand;
- **a brand-new pool** (yellow) — unproven.

**Passing every check is not investment advice.** A memecoin has no
fundamentals for this to evaluate — `check` only rules out the specific,
well-known scam mechanics above. Most tokens that pass it still go to zero on
momentum decay alone; use it to avoid the obvious traps, not to pick winners.

**On "multi-token" vs. arbitrage:** true cross-DEX arbitrage on Solana is a
speed contest against professional MEV infrastructure — not something worth
attempting here. `multi-buy` is the honest version of "spread bets across
several coins instead of one": it buys `--count` trending tokens (filtered by
a minimum-liquidity floor, ranked by volume) or an explicit `--tokens` list,
each through the exact same per-trade ceiling and cumulative wallet budget as
a single buy. One bad token (no liquidity route, a guard trip) is reported and
skipped — it never stops the rest of the list or bypasses the budget.

**A note on testing:** the sign-and-submit path could not be exercised
against the live Solana network while building this (this dev environment's
proxy blocks those hosts) — the request/response shapes follow Jupiter's and
Solana's documented, stable APIs, and every piece of logic around it (caps,
routing, guards) is unit-tested offline. Before your first real trade: run
`hf-bot memecoin wallet` to confirm the RPC connection and balance read work,
then use `--dry-run` at least once.

### Trade notifications

Every order that actually reaches the broker fires a notification — manual
approvals and `--auto-execute` placements alike — so a fired trade is never
silent. A **Windows desktop toast** fires with zero setup. For your phone too,
set `VANTRIX_WEBHOOK_URL` in `.env` to a Discord/Slack incoming webhook or an
[ntfy.sh](https://ntfy.sh) topic URL — one URL works for all three (see
`.env.example`). Both channels are best-effort: a notification failure never
blocks or undoes a trade that already placed.

### Reaching it from anywhere (secure tunnel)

The dashboard can dispatch committee reviews, so **never expose it without a
token**. One command does auth + tunnel together (or double-click
`run-dashboard-remote.bat`):

```bash
hf-bot dashboard --auth --tunnel --open
```

This forces a token on, starts a `cloudflared` quick tunnel (anonymous, no
Cloudflare account), and prints a ready-to-open public link:

```
PUBLIC LINK (open on any device): https://something.trycloudflare.com/?key=<TOKEN>
```

Open that on your phone or any browser. The `?key=<TOKEN>` sets a cookie, so
you only paste it once per device. Every request — the page, the live poll, and
command dispatch — is rejected without it (`401`), with constant-time token
comparison.

**Token safety:** the dashboard itself spends **no Claude tokens** — it serves
the HUD and *queues* commands; a review only spends tokens when you run it in a
Claude session. `--tunnel` refuses to run together with `--enable-agent-runner`
for exactly this reason: nobody with the link should be able to spawn Claude on
your account. (Prefer to wire the tunnel yourself? `cloudflared tunnel --url
http://localhost:8420` or `ngrok http 8420` against `hf-bot dashboard --auth`.)
The tunnel and your PC must stay running; for always-on access, host it on a
small server instead.

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

### The APEX console — speak to your committee

The Stark HUD's command bar is a **console to APEX** (the CIO / orchestrator).
Type a free-form command — `review ASTS and size it`, `how are we tracking vs
SPY?`, `have the team study macro` — and APEX acts on it, delegating to the
specialists, and **reports back in one voice** in the console feed. You hear
from APEX, not from eleven agents.

The honest architecture: the Python server has no LLM, so it cannot run the
committee itself. Your message is stored, then a Claude session executes APEX,
which emits the committee events that light the cortex and returns a reply
captured in the console.

- **Off by default (queue):** the command queues for a Claude session to run
  (`hf-bot committee queue --run`). Zero tokens until you run it.
- **`--enable-agent-runner`:** the server spawns `claude -p` so **APEX runs the
  moment you command** — *issuing the command is your authorization to spend*.
  Needs the `claude` CLI installed and logged in.

**Your command is the authorization.** Nothing spends tokens on its own; each
message you send is explicit consent for that one run. The message is passed to
the executor as a single argument (never a shell), so free-form text is safe.
On a tunnel the runner is allowed only *with* a token — so only you, holding the
link and key, can authorize a spend. Double-click **`run-command-center.bat`**
for the full experience (token + public link + runner).

The spawned `claude -p` runs with `--permission-mode bypassPermissions` so APEX
can use committee tools without interactive prompts (a background process has no
TTY to answer them). If APEX stalls or your Claude version rejects that mode,
override it: `--runner-permission-mode default` (you'll then answer prompts in
the dashboard's terminal) or another mode your version supports.

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

### Tiered models — cheap for research, Claude for the hard calls (`hf-bot models`)

The committee is expensive because one question fans out into many tool-driven
Claude calls. But a lot of the work — researching a curriculum topic, a plain
"what is X" — doesn't need Claude. Point a cheap model at that work and save the
Claude budget for the calls that actually decide capital.

Configure providers in `.env` (see `.env.example`):

```
NVIDIA_API_KEY=…   NVIDIA_MODEL=nvidia/nemotron-…    # cheap tier — research/study
OLLAMA_API_KEY=…   OLLAMA_MODEL=qwen2.5-coder        # mid tier — screening
# (or OLLAMA_BASE_URL=http://localhost:11434/v1 for a local Ollama, no key)
```

```bash
hf-bot models check                       # which tiers are live; pings them
hf-bot models route "should I buy NVDA"   # show where a question would route
hf-bot models study --agent equity-analyst   # study on the cheap tier — writes
                                             # the note and records it. 0 Claude tokens.
hf-bot models study-cycle --rounds 3      # the free trickle across least-covered agents
```

Unlike a plain Ollama fallback (which can only *talk*), `models study` has the
**harness** do the tool work — it picks the next topic, asks the cheap model for
the note body, then writes `knowledge/<agent>/<slug>.md` and records it. So a
non-tool model genuinely **grows the library** (the Knowledge panel ticks up).
Each such note carries a header stating which model wrote it and that it's a
distillation, not live-researched or committee-reviewed.

**In the dashboard:** add `--tiered` to `hf-bot dashboard`. STUDY buttons then
research on the cheap tier (no Claude tokens), and plainly informational console
questions answer on the cheap tier too. Anything decision-shaped — buy/sell,
valuation, "best stock", committee — still routes to Claude. Routing is
safety-first: when a console question is ambiguous, it goes *up* to Claude, never
down. If a cheap tier errors, it falls back to the `claude` executor when one is
available, so a click never silently no-ops.

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
