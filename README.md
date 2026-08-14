# hf-trading-bot

A systematic trading bot: a pluggable strategy engine, portfolio risk
management (drawdown circuit breaker, daily/weekly loss guards, ATR-based
position sizing), a backtester, and a broker abstraction — safe simulated
paper trading by default, with an explicitly opt-in Robinhood live adapter.

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

- `PaperBroker` (default) — simulated fills on real market data via
  `yfinance`. No credentials, no network calls to any brokerage, cannot
  place a real order by construction.
- `RobinhoodBroker` — live trading via the unofficial `robin_stocks` client.
  Refuses to initialize unless `HF_BOT_I_UNDERSTAND_LIVE_TRADING=true` is
  set, plus `ROBINHOOD_USERNAME` / `ROBINHOOD_PASSWORD`. Install with
  `pip install -e ".[live]"`.

Both implement the same `Broker` interface (`hf_trading_bot/broker/base.py`),
so the strategy engine and risk logic never change when you switch brokers.

## Live Robinhood trading

**Everything below risks real money. Validate on backtests and paper mode
first.** The kill switch defaults to ON (paused) — the bot will not place a
live order until you explicitly turn it off.

### One-time setup

1. `pip install -e ".[live]"` to add `robin_stocks`.
2. Create a `.env` file in the repo root (already gitignored — never commit it):
   ```
   ROBINHOOD_USERNAME=you@example.com
   ROBINHOOD_PASSWORD=your-password
   HF_BOT_I_UNDERSTAND_LIVE_TRADING=true
   ```
3. Set `broker: robinhood` in `config/settings.yaml` (copy it from
   `settings.example.yaml`, which includes a worked small-account risk block).
4. Run the one-time interactive login, which prompts for your MFA code:
   ```bash
   hf-bot robinhood-login
   ```
   This saves a session to `~/.tokens/robinhood.pickle` so later unattended
   runs don't need MFA. **Treat that file as a credential** — anyone who has
   it can trade your account. It stays on the machine that created it; never
   commit, sync, or copy it anywhere.
5. Dry-run against the real account (reads balances/positions, places nothing):
   ```bash
   hf-bot run
   ```
6. When you're satisfied: `hf-bot kill-switch --off`, then
   `hf-bot loop --live --interval 3600`.

### Where to run it

Run the loop on a machine you control that stays on — a home server, a VPS,
or a laptop that doesn't sleep. Don't run it in an ephemeral cloud container:
when the container is reclaimed the loop dies silently and the saved session
is wiped, and Robinhood's device checks tend to force re-verification when
logins come from a new/rotating IP, which an unattended process can't answer.

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
take meaningful positions in high-priced symbols. Fractional orders on
Robinhood are **market-only** — there's no broker-side stop order for them.
Stops are therefore enforced in software: the stop price is recorded at
entry and checked at the start of every cycle against the session low. This
means **a stop only triggers when the bot runs** — a gap down while the
process is stopped is not protected. Keep the loop running during market
hours, and size positions on the assumption that stops are best-effort.

## Backtesting

```bash
hf-bot backtest QQQ --strategy rsi_mean_reversion --start 2021-01-01 --end 2026-01-01
```

Reports total trades, win rate, CAGR, Sharpe, and max drawdown from a
long-only, one-position-at-a-time, $10k-notional-per-trade replay
(`hf_trading_bot/backtest.py`). Always validate a strategy this way — and
then in paper mode over real time — before ever pointing it at
`RobinhoodBroker`.

## Tests

```bash
pytest
```

Covers the risk math (circuit breaker, position sizing, weekly guards),
each strategy's entry/exit logic on synthetic price series, and the
backtest replay/stats engine.

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
