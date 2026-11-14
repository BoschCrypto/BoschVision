# Can a team of agents reading different data find high-probability setups?

14 November 2026. Run exactly as fixed in `PLAN.md`, which was committed
before any of the new data was examined.

## Verdict

**No. Nothing cleared the bar, and the null is cleaner than the last one.**

Not one of 290 testable conditions reached the required t-statistic of 3 on
both assets. Not one reached even 2 on both. The largest t-statistic found
anywhere was 2.10, on a single quintile of one agent on one asset.

The telling number: if every one of those 290 conditions were pure noise,
the largest |t| among them would average **3.08**. The observed largest was
**2.10** — fewer extreme results than chance alone would produce. There is
no edge here being obscured by noise; there is less signal than noise would
have manufactured.

## Why this null is worth more than the previous one

The last system could be dismissed with "your agents were redundant" — QUANT
and CHARTIST both read price, so their agreement added nothing. That
criticism was correct and it was the reason for this study.

This time the agents were genuinely independent, and that was measured
rather than assumed:

| | TREND | FLOW | CROWD | FEAR | MACRO | BREADTH |
|---|---|---|---|---|---|---|
| **TREND** | 1.00 | -0.01 | 0.05 | -0.08 | -0.10 | 0.36 |
| **FLOW** | -0.01 | 1.00 | 0.07 | 0.11 | 0.09 | -0.12 |
| **CROWD** | 0.05 | 0.07 | 1.00 | -0.21 | -0.46 | 0.32 |
| **FEAR** | -0.08 | 0.11 | -0.21 | 1.00 | 0.39 | -0.21 |
| **MACRO** | -0.10 | 0.09 | -0.46 | 0.39 | 1.00 | -0.34 |
| **BREADTH** | 0.36 | -0.12 | 0.32 | -0.21 | -0.34 | 1.00 |

Average correlation between different agents: **0.19** on BTC, 0.22 on ETH.
The highest pair is MACRO and FEAR at 0.46, which is expected — both
measure how nervous markets are. TREND and FLOW are essentially unrelated.

So the design did what it was meant to: six genuinely different views,
combined by conjunction rather than averaging. It still predicts nothing.
That rules out the obvious explanation for the earlier failure.

## Results

Daily decisions, 14 Nov 2023 to 5 Nov 2026, 723 usable days per asset after
the 365-day ranking warm-up. Forward returns start at the next minute's
open. Statistics cluster by calendar week. Cost is 24 bps a round trip.

**The base rate.** Simply holding, every day: BTC +9.8 bps a day
(t = 1.39), ETH +6.8 (t = 0.66). Even unconditional crypto drift is not
statistically distinguishable from zero over this window.

**When agents agree**, scored in the agreed direction, gross / net of costs:

| Agents agreeing | 1 day BTC | 1 day ETH | 7 days BTC | 7 days ETH |
|---|---|---|---|---|
| 2 or more | -2 / -26 | +15 / -9 | -2 / -26 | +63 / +39 |
| 3 or more | +5 / -19 | -4 / -28 | -20 / -44 | +61 / +37 |
| 4 or more | -9 / -33 | +19 / -5 | **-112 / -136** | **+76 / +52** |
| 5 or more | +39 / +15 | +59 / +35 | -90 / -114 | +172 / +148 |

Two things to read here. First, more agreement does not improve the
outcome — the pattern wanders rather than strengthening, which is what
noise looks like. Second, and more decisively, at the 7-day horizon with
four or more agents agreeing, **BTC is strongly negative and ETH strongly
positive under identical conditions**. A real effect does not reverse sign
between two assets that move together.

The apparently attractive cells (5+ agree) have t-statistics of 0.87 to
1.09 on 25 to 46 events. That is noise with a nice-looking mean.

**Best pairs of agents**, by gross return, all horizons: the strongest was
CROWD+BREADTH at 7 days (BTC +61 gross, ETH +231), t = 0.85 and 1.64 on 37
and 34 events. Nowhere near the bar.

## What was tested

| | |
|---|---|
| Conditions examined | 290 with usable statistics, of 300 planned |
| Agents | 6, reading 6 different data sources, 3 new to this system |
| Assets | BTC and ETH |
| Horizons | 1, 3 and 7 days |
| Window | 14 Nov 2023 to 5 Nov 2026, 723 usable days per asset |
| Bar | net > 0, t > 3, 30+ non-overlapping events, **on both assets** |
| Conditions clearing it | **0** |
| Conditions reaching even t > 2 on both | **0** |

## Limits of this result

- **Three years, two assets.** A longer window would give more events,
  particularly at the 7-day horizon, where some conditions fired only 25 to
  46 times. The binding constraints were the implied-volatility and VIX
  histories.
- **The window is not a clean holdout.** The 2025-26 portion was used by
  the previous study. That study examined short-term *price* rules, which
  says little about funding, volatility or breadth conjunctions, but this
  is not a pristine test and is not described as one.
- **Breadth is survivor-biased:** the 400 Coinbase pairs are those listed
  today.
- **Thresholds were fixed in advance** (`PLAN.md` section 3) and not tuned.
  Tuning them would almost certainly produce something that looks
  significant, which is exactly why they were fixed.
- This tests **conjunction of levels**. It does not test interactions, time
  of day, event-driven conditions, or any data I could not reach:
  order-book depth, on-chain flows, options skew, or news with reliable
  timestamps.

## Conclusion

The agent-team idea was tested properly rather than dismissed. The agents
were made genuinely diverse, the diversity was verified by measurement, and
they were combined by intersection rather than by averaging — the design
the earlier failure pointed to. The result is a clean null: less apparent
signal than random noise would have generated across the same number of
tests.

Per the decision taken before the run: **report and stop.**

## Reproduce

```bash
python -m velocity.teamwork.data dvol    TEAM_DIR
python -m velocity.teamwork.data breadth TEAM_DIR --pairs 400
# VIX comes from the IBKR connector and is saved as TEAM_DIR/VIX_daily.csv
python -m velocity.teamwork.panel M1_DIR TEAM_DIR FUNDING_DIR TEAM_DIR/panel.npz
python -m velocity.teamwork.study TEAM_DIR/panel.npz velocity/teamwork/results
```

Full numbers for every condition: `results/study.json`.
