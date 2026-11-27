# Where the Apex contracts can actually be traded — 2026-11-10

Checked live through the Interactive Brokers connector. **Resolves the open
question from `apex-costs-research-2026-11-10.md` §6.2: the nano Ether
contract DOES exist.** The ETH engine is tradeable; no part of the backtest
needs discarding.

## Available at IBKR, exchange COINDERIV (Coinbase Derivatives)

| symbol | contract | size | expiries listed |
|---|---|---|---|
| **BIP** | nano Bitcoin BTC **Perpetual** Futures | 0.01 BTC | one contract, 2089-12-30 — effectively none |
| BIT | nano Bitcoin Futures | 0.01 BTC | 2026-11-27, 2026-12-25, 2027-01-29 |
| **ET** | nano Ether Futures | 0.1 ETH | 2026-11-27, 2026-12-24, 2027-01-29 |

Also listed: BTI (full-size Coinbase Bitcoin), CBBR and CETBR (the benchmark
rate indices the contracts settle against).

## CME alternatives, also at IBKR

| symbol | contract | size |
|---|---|---|
| MBT | Micro Bitcoin | **0.1 BTC** |
| MET | Micro Ether | **0.1 ETH** |

**CME is more liquid but the wrong size for this account.** MBT is 10x the
nano, ~$7,480 of notional per contract. At the system's sizing (BTC notional
is roughly 0.25x equity) a single MBT contract implies an account near
**$30,000** before the BTC engines can trade at all — against ~$5,000 on the
Coinbase nano. For an account of Waldo's size, Coinbase nano is the right
venue and CME is not an option yet.

MET at 0.1 ETH matches the simulator's ETH contract size exactly.

## The structural mismatch that remains

**BTC has a perpetual (BIP); ETH does not.** Only monthly ET contracts were
found. The simulator charges continuous hourly carry, which describes a
perpetual. So:

- **BTC:** use **BIP**, not BIT. It matches the model's design — no expiry, no
  roll, hourly funding.
- **ETH:** monthly ET means a **roll every month**, with its own spread and
  slippage that the backtest does not model at all. The ETH engine produced
  +$12,512 of the $33,333 five-year profit, so this cost is not trivial and is
  currently unquantified.

Estimating the roll cost is the next piece of work: roughly 12 extra round
trips a year on the ETH sleeve, at the measured spread plus fees.

## Measured liquidity (BIT front month, 2026-11-10, REALTIME)

bid 74,795 / ask 74,815 — **$20 spread, 0.027%**, 241 contracts bid by 28 ask,
19,002 traded today, 115,986 open interest. Deep relative to a system trading
3-11 contracts.

**Not yet measured:** live quotes on BIP and ET. Both snapshot calls returned
`Service Unavailable` from IBKR, a transient error — the BIT quote on the same
connector succeeded minutes earlier. Retry before relying on BIP/ET spreads;
do not assume they match BIT's.

## Still unconfirmed

- IBKR's own commission per contract on COINDERIV products.
- Whether IBKR permits API-driven systematic trading of these contracts on a
  retail account, and under what approvals.
