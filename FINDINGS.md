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
