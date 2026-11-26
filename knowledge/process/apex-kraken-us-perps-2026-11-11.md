# Kraken US perpetual futures vs Coinbase nano — 2026-11-11

Read from Kraken's own support pages (us-futures-fees, updated 2026-10-30;
contract-specifications, updated 2026-11-03; us-futures-101). Supersedes the
venue conclusion in `apex-venues-2026-11-10.md`.

Structure: contracts listed on **Bitnomial Exchange** (CFTC Designated Contract
Market), cleared by **NinjaTrader Clearing LLC dba Kraken Derivatives US**
(CFTC-registered FCM, NFA ID 0309379), traded through Kraken Pro. Launched for
US customers 2026-06-15.

## Fees — quoted verbatim

**Bitnomial-listed perpetual futures: all-in $0.0113 per contract per side**,
comprising commission $0.0003, exchange/clearing $0.001, NFA $0.01.

Coinbase nano via IBKR is roughly **$0.35–$0.59 per side** (exchange $0.10 plus
broker). Kraken is about **30x cheaper**.

## Contract sizes — the catch

| | Kraken (Bitnomial) | Coinbase (COINDERIV) |
|---|---|---|
| BTC | PBTCUCZ50, **0.01 BTC** | BIP perp, 0.01 BTC |
| ETH | PETHIUZ50, **0.5 ETH** | ET, **0.1 ETH** — but MONTHLY, not perp |

Kraken's ETH contract is **5x larger**, so position sizing is far coarser on a
small account. Both of Kraken's are true perpetuals with no expiry and no roll.

## Measured, five years, same engine logic, only the venue differing

Coinbase column is **flattered** — the monthly ETH roll (~12 extra round trips
a year) is not modelled at all.

| account | Kraken CAGR | trades | ETH trades | Coinbase CAGR | trades | ETH trades |
|---:|---:|---:|---:|---:|---:|---:|
| $3,000 | 35.8% | 311 | 81 | 33.6% | 331 | 101 |
| $5,000 | 39.5% | 318 | 88 | 33.8% | 332 | 102 |
| $10,000 | 38.4% | 328 | 98 | 34.2% | 332 | 102 |
| $20,000 | 36.8% | **332** | **102** | 33.9% | 332 | 102 |
| $50,000 | 37.8% | 332 | 102 | 33.7% | 332 | 102 |

(332 trades / 102 ETH trades is the full undegraded run.)

**Read it as:** Kraken is worth roughly **+3 points a year** at size, purely on
fees. But it only executes the strategy faithfully from about **$20,000**;
Coinbase is faithful from about **$3,000–5,000**. The higher Kraken CAGR at
$5,000 is a rounding artifact from lumpier bets, not an edge — it skips 14 of
102 ETH trades there.

Note also that total cost is dominated by the **10%/yr carry charge**, not
fees, which is why a 30x fee cut moves 5-year costs only ~28%. And that carry
figure is itself too harsh: measured funding is ~4.5%/yr (BTC) and ~2.6% (ETH),
and shorts should *receive* it.

## Mechanics confirmed

- **Stop Loss orders are supported**, as are Trailing Stop and Trailing Stop Limit. The strategy's protective stops can be placed natively.
- **Good-Till-Cancelled is NOT supported on perpetual futures.** Only day orders and IOC. Stops must be re-placed each session — a real implementation requirement, not a blocker.
- Funding: **settled once daily as a single cash adjustment at 3:00pm CT** (not the 8-hourly schedule used on non-US Kraken Futures).
- Trades 24/7 subject to maintenance windows; initial (higher) margin applies during them.
- 17 perpetual contracts at launch: BTC, ETH, SOL, XRP, ADA, LINK, DOGE, LTC, AVAX and others.

## NOT confirmed — Waldo must check these directly

1. **Is Connecticut an eligible state?** No eligibility list found on these pages.
2. **Is API / automated trading permitted** on these contracts on a retail account? Kraken markets "API Trading" generally; nothing found specific to Bitnomial perps.
3. **Minimum account balance** to open US futures.
4. **Initial and intraday margin per contract** — stated to be on the per-instrument page inside Kraken Pro, not public.
5. **Tax treatment.** Cleared through a CFTC FCM on a DCM, which usually implies Section 1256 — but this is a new product structure and must be confirmed with a professional.

## Recommendation

**Kraken is the better venue, but not until roughly $20,000.** Below that the
0.5 ETH contract drops trades. Since funding is not planned before mid-2027 and
the account would start near $5,000, the honest position is: plan on Kraken,
verify the five unknowns above first, and accept that at $5,000 the ETH sleeve
runs at reduced fidelity on either venue.
