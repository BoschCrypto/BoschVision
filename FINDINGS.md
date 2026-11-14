# Findings

Dated conclusions from testing, kept here so they don't quietly get
re-litigated (and re-paid for) a year later. Each entry is append-only —
if a later test contradicts one, add a new entry, don't edit the old one.

## 2026-08-14 — Five systematic strategies do not beat buy-and-hold

**Test:** `hf-bot sweep`, 24 symbols, 5 strategies, 2021-01-01 → 2026-08-14.

| strategy | beat buy&hold | hit rate | median excess | trades |
|---|---|---|---|---|
| momentum_90d | 5/24 | 21% | −71.2 pts | 680 |
| rsi_mean_reversion | 8/24 | 33% | −37.4 pts | 185 |
| sma_crossover | 6/24 | 25% | −51.0 pts | 360 |
| macd_crossover | 7/24 | 29% | −35.6 pts | 1,294 |
| bollinger_breakout | 9/24 | 38% | −57.9 pts | 591 |

A coin flip is 50%. Every strategy landed below that, with large negative
median excess, and the result held across five mechanically different
signals — that consistency is what makes it a finding rather than one bad
draw.

**Audit.** Reviewed `backtest.py` against seven specific failure modes
before trusting this:

1. **Open-trade exclusion — real bug, fixed.** `stats()` was dropping any
   position still open at the window's end from `total_return_pct`
   entirely, while buy-and-hold — which never has an "open position"
   problem — always captures the final price. This specifically penalized
   trend-followers in this rising window. Fixed: open positions are now
   marked to the final close for the equity curve (kept out of realized
   win/loss stats, since they haven't resolved). Did not flip the
   conclusion — it can only ever mis-price one trade per symbol per
   strategy, nowhere near enough to close a −71pt median gap.
2. **Look-ahead bias — favors the strategies.** Signal and entry both use
   the same bar's close. Real execution would be worse, not better, so
   the true gap is probably understated here, not overstated.
3. **Cash drag — penalizes the strategies, small.** Idle cash between
   trades earns 0% in the sim vs. buy-and-hold's constant full exposure.
   Crediting T-bill yields for idle periods adds at most a few points over
   5.5 years — not enough to matter against these gaps.
4. **Compounding model — consistent.** Same sequential full-reinvestment
   convention as `buy_and_hold()`; the two numbers are comparable.
5. **Regime dependence — real caveat.** 2021–2026 is a strong bull window,
   which is exactly where anything that steps out to cash struggles
   against buy-and-hold. Limits how far this generalizes to other
   regimes; doesn't invalidate what was tested.
6. **Transaction costs — unmodeled, immaterial at this scale.** Even
   macd_crossover's 1,294 trades add roughly 1pt of cumulative drag per
   symbol at plausible slippage — far short of a −35.6pt median.
7. **No other bugs.** Benchmark and strategy windows use the identical
   fetched bar list; `years` is computed once and shared.

**Verdict: robust.** Every bias found either favors the strategies or is
too small to matter. This does not mean no systematic strategy could ever
work — it means these five specific rules, over this specific window, on
this specific universe, did not earn their keep. Re-testing the same five
rules again without a new hypothesis is data-mining, not validation.

**Decision:** pivot the account to an index core, keep these strategies as
the measured baseline (never delete — they're what any future rule has to
beat), and route active decisions through the research committee +
decision journal instead. See `hf-bot portfolio compare` for the ongoing
scorecard against SPY.

## 2026-11-12 — A five-agent intraday swarm has no edge; costs are the whole loss

**Test:** `velocity` (new package). Five cooperating agents on one-minute
BTC/ETH bars, Coinbase perpetual-style futures, 10 bps per side. Six runs
across 90 days of real data, in-sample and out-of-sample.

| | Trades | Before costs | Net |
|---|---|---|---|
| Six runs, BTC and ETH | 892 | −$122 | **−$1,188** |

Flat before costs; fees of $1,065 were the loss. A null test on a driftless
random walk lost its costs and nothing more, so the harness is not inventing
signal. Fixing eight real defects (kill switch not flattening, VWAP never
resetting on a 24-hour market, a loss streak that could never clear) made
the machine work as designed and it then lost *more*, because the earlier
bugs had been sidelining it and saving fees by accident.

## 2026-11-13 — Nothing on the 5-minute to 1-hour horizon pays for its own costs

**Test:** `velocity/shortterm/`. 30 configurations in five economically
distinct families (trend pullback, volatility breakout, failed-breakout
fade, hourly trend, channel breakout). Walk-forward selection on 27 Nov 2023
– 7 Apr 2026, one final test on 8 Apr – 13 Nov 2026 after freezing.

| Family | Gross per trade | Walk-forward out of sample |
|---|---|---|
| Trend pullback | −2 to +1 bps | −83.4%, 0 of 7 folds up |
| Volatility breakout | +1 to +5 | −36.2%, 0 of 7 |
| Failed-breakout fade | −3 to −1 | −23.0%, 0 of 7 |
| Hourly trend | +11 to +15 | −8.5%, 3 of 7 |
| Channel breakout | +6 to +18 | −14.8%, 2 of 7 |

A round trip costs 22–26 bps. Nothing qualified. The frozen rule lost 13.29%
on its final test, and its **gross edge there was +0.58 bps per trade** — so
even free trading would not have made it profitable. The existing five-agent
system lost 7.92% over the same window.

The ±0.28 arbiter threshold is worth 0–3 bps; CHARTIST's vote adds about
4 bps on ETH and nothing on BTC; neither agent's confidence predicts
returns. CHARTIST reads 95%+ confidence on a third of all samples because
its confidence measures how much chart structure exists, not how strongly
that structure points anywhere.

## 2026-11-13 — Carry and funding signals do not beat cash

**Test:** `velocity/research/`. Deribit funding history from 2019, Binance
as a cross-check. Specs locked on research data, holdout run once.

| | Holdout (2025 – Nov 2026) | Full history (2020 – 2026) |
|---|---|---|
| 20-day breakout | **+2.7%/yr** | +25.4%/yr |
| Funding carry | +0.9%/yr | +3.8%/yr |
| Buy fear only | −10.8%/yr | +12.2%/yr |
| Buy and hold | −15.9%/yr | +42.3%/yr |
| Cash | +4.2%/yr | +3.1%/yr |

Funding carry earns a real but tiny yield, below Treasury bills. Shorting
crowded funding lost money. The breakout rule returned most of the three but
is **crash insurance, not a forecast**: it lost 29% in 2018 against BTC's
75% and 43% in 2022 against 68%, while giving up a large share of every bull
market. That is a risk property worth having if you want crypto exposure
anyway; it is not an edge.

## 2026-11-14 — Six genuinely independent agents, combined by conjunction, still predict nothing

**Test:** `velocity/teamwork/`. The obvious criticism of the earlier swarm
was that its agents all read price, so their agreement was close to a
tautology. This gave six agents six different inputs — price, volume,
funding, implied volatility (Deribit DVOL), VIX, and breadth across 400
Coinbase pairs — and combined them by intersection rather than by averaging.
723 usable days per asset.

Independence was measured, not assumed: **average correlation between agents
0.19** (max 0.46, between the two volatility measures). The design worked.

| | |
|---|---|
| Conditions tested | 290 |
| Clearing the bar (net > 0, t > 3, 30+ events, both assets) | **0** |
| Reaching even t > 2 on both assets | **0** |
| Largest \|t\| found anywhere | **2.10** |
| Largest \|t\| that 290 pure-noise tests would average | **3.08** |

Fewer extreme results than chance alone would manufacture. The clearest tell:
at 7 days with four or more agents agreeing, BTC returned −112 bps and ETH
+76 bps under identical conditions. A real effect does not reverse sign
between two assets that move together.

## What these four entries mean together

Four studies, each with its plan committed before the data was touched and
its bar set in advance. Horizons from one minute to 28 days, price-based
signals, funding, volatility, cross-asset and breadth. All negative after
costs.

The apparatus demonstrably works: null tests return nothing, and a positive
control confirms it finds effects that are really there. The repeated answer
is therefore evidence, not a run of bad luck.

Untested, so nothing is claimed about them: order-book depth, on-chain
flows, options skew, and news with reliable arrival timestamps. Each needs
data access this setup does not have.

**Standing conclusion:** no strategy in this repository should be run with
real money. The live view (`velocity mind`) therefore watches by default and
takes no positions.
