# The equity liquidation — 2026-10-30 to 2026-11-02

Every leg below was **read back from the broker's own order records**
(`get_equity_orders`, both accounts), not inferred from balance differences.
That distinction matters: on the morning of 11-02 I could see three of the
seven legs only as a change in cash, and the standing rule is that an order is
not placed until the broker says so. It now says so for all seven.

## The fills

| sym | acct | units | fill | proceeds | basis | realised | when (ET) |
|---|---|---:|---:|---:|---:|---:|---|
| NVDA | ••••9528 | 0.372740 | 204.93 | 76.39 | 72.90 | **+3.49** | Fri 10-30 17:23 ext |
| QQQ | ••••2596 | 0.721429 | 691.00 | 498.51 | 499.54 | **−1.03** | Fri 10-30 17:24 ext |
| VOO | ••••2596 | 0.823417 | 694.95 | 572.23 | 552.11 | **+20.12** | Fri 10-30 17:25 ext |
| VTI | ••••9528 | 1.990520 | 373.36 | 743.18 | 750.00 | **−6.82** | Mon 11-02 09:30 open |
| VGT | ••••9528 | 0.565032 | 117.55 | 66.42 | 65.35 | **+1.07** | Mon 11-02 09:30 open |
| VTI | ••••2596 | 2.642845 | 373.36 | 986.69 | 1,000.00 | **−13.27** | Mon 11-02 09:30 open |
| VGT | ••••2596 | 5.599784 | 117.55 | 658.25 | 627.81 | **+30.44** | Mon 11-02 09:30 open |
| | | | | **3,601.67** | | **+34.00** | |

Fees: **$0.00** on every leg. All four Monday legs executed at the opening
print, 09:30:00–09:30:03 ET, as market orders entered Friday evening.

## Two independent confirmations

The broker's own realised-P&L series agrees to the cent on the account it
covers in a bucket of its own:

- ••••2596, ET day 10-30: broker **+19.09** / 2 trades. QQQ −1.03 + VOO +20.12 = **+19.09**.
- ••••2596, ET day 11-02: broker **+17.17** / 2 trades. VTI −13.27 + VGT +30.44 = **+17.17**.

And `unsettled_funds` matches the Monday proceeds exactly:

- ••••9528: 743.18 + 66.42 = **809.60** = reported unsettled.
- ••••2596: 986.69 + 658.25 = **1,644.94** = reported unsettled.

Monday's buying power likewise equals *Friday's* proceeds in each account
(76.39 and 1,070.74), which is T+1 settlement doing exactly what it should.

## State after

| | ••••9528 | ••••2596 | ••••6224 |
|---|---:|---:|---:|
| cash | 885.99 | 2,715.66 | 0.24 |
| buying power (settled) | 76.39 | 1,070.72 | — |
| unsettled until Tue 11-03 | 809.60 | 1,644.94 | — |
| equity | **0** | **0** | 0 |
| crypto | 0 | 0.0031 dust | 0 |

**The equity book is empty.** Total visible Robinhood book **$3,601.89**, all
of it cash, 68% of it unsettled until Tuesday.

## What this breaks

The risk gate is now measuring against a fraction of his wealth. The $7,065
designated reserve exceeds the entire visible book, so risk capital computes to
**−$3,463.94** and the gate HALTs on arithmetic rather than on judgement. It
has now done so ten times with an identical reading, which is the signature of
a limit that has stopped being informative.

The fix is not to loosen the limit — it is to point it at the whole book. That
needs the Schwab balance, which no connector in this environment reaches.
**Waldo has to report that figure by hand**, and until he does, the gate's
output should be read as "not measurable", not as "too much risk".
