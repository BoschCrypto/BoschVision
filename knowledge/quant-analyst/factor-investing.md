# Factor investing — is the edge alpha or repackaged beta

*Studied by: quant-analyst (CIPHER). Core seed note; expand via
`hf-bot study next --agent quant-analyst`.*

## Key principle

Most claimed "edges" are exposures to well-documented factors — value, size,
momentum, quality/profitability, low-volatility — that carry a risk premium you
could have bought cheaply. Before crediting a strategy with skill, you must
strip out what a few factor tilts already explain. Paying active fees (or
taking active risk) for what is really beta is the most common self-deception
in systematic investing.

## Concrete anchors

- **Fama-French.** Market, size, and value factors explain a large share of
  cross-sectional return variation; later work added profitability and
  investment. A strategy's return should be regressed against these before
  calling any residual "alpha."
- **Momentum (Jegadeesh-Titman; AQR).** Past 6-12 month winners tend to keep
  winning short-term — a robust, independent factor, but one with violent
  crashes at reversals. Real, but not free.
- **Quality-minus-junk.** Profitable, stable, growing, well-managed firms
  outperform their opposites; much of what looks like stock-picking skill is a
  quality tilt.

## What this changes about how I (CIPHER) operate

- I decompose any proposed edge into factor exposures first; only the residual
  is a candidate for genuine alpha.
- I am skeptical of backtests that quietly load on a single factor and present
  its premium as discovery.
- I remember factors are regime-dependent and can underperform for years — a
  known premium is not a guaranteed one.

## Sources

Fama & French factor papers; Jegadeesh & Titman (momentum); AQR research
library (value/momentum, quality-minus-junk). Public academic literature; no
proprietary data.
