# The Plan — built 2026-09-29

Sources: Robinhood account ••••9528 (live), Apex Home (`5a17bf81-20e0-4485-b2c6-3d6d0ff868c2`),
and the measured findings in `FINDINGS.md`. Every number here is read from one of those,
not estimated. Where something is an estimate it says so.

---

## Where you actually stand

| | |
|---|---|
| Age | 22 |
| Net worth (Apex Home) | **$8,620** |
| Brokerage account | $2,197.49 — VTI $750, NVDA $87, VGT $71, cash $1,289 |
| Fixed monthly expenses | **$2,355** (rent $1,835 + $520 of recurring) |
| Emergency fund | **$0** |
| Roth IRA contributed | **$0** of a $7,000 limit |
| New savings capacity | **$2,000/month** |

## Your stated goal, and the honest arithmetic

Apex Home records: **$1,000,000 net worth by age 30** (target date 2034-06-18).

That is 8 years and 96 months away. At $2,000/month starting from $8,620:

| annual return | you reach | verdict |
|---|---|---|
| 7% | $271,464 | short by $728,536 |
| 9% | $297,374 | short by $702,626 |
| 12% | $342,260 | short by $657,740 |
| 15% | $395,689 | short by $604,311 |
| 20% | $508,713 | short by $491,287 |
| **25%** (impossible) | **$661,320** | **still short by $338,680** |

**The gap is not a returns problem.** Even at a fantasy 25% a year — roughly Buffett's
lifetime record, sustained, with none of his bad decades — you land at two thirds of the
target. No investment strategy on earth closes it.

What closes it: **$7,024/month.** That is 3.5x the current plan, or $84,287/year saved on
top of $35,388/year of living costs. So the goal requires roughly **$120,000/year of
income**, which is an earning question, not an investing one.

### What $2,000/month actually buys

| milestone | age |
|---|---|
| $25,000 | 22.7 |
| $50,000 | 23.6 |
| $100,000 | **25.2** |
| $250,000 | 29.1 |
| $500,000 | 33.5 |
| $1,000,000 | **39.1** |

$1M by 39 rather than 30. Stated plainly: **saving $2,000/month at 22 puts you ahead of
almost everyone your age.** The number that has to move for age 30 is income, and that is
the part this project has never once worked on.

---

## THE CHECKLIST

### Phase 0 — do these this week
- [ ] **Open a Roth IRA.** Free, ~10 minutes, no funding required to open. Apex Home
      already records the $7,000 limit and $0 contributed.
- [ ] **Open a high-yield savings account** for the emergency fund. Paying ~4% with the
      Fed at 3.75–4.00%, so the buffer is not dead money.
- [ ] **Move the $1,289 of idle brokerage cash into it.** That is 55% of the emergency
      target already sitting in the wrong account earning nothing.
- [ ] **Set up the automatic $2,000/month transfer.** Automatic beats large.
- [ ] **Fix two data errors in Apex Home** (see "Corrections owed to the app" below).

### Phase 1 — emergency fund, roughly months 1–7
- [ ] Target **$14,130** = 6 × $2,355. Your profile says 6 months; honour it.
- [ ] The app's "Phase 1: Emergency fund to $3,000" goal is **wrong** — $3,000 is 1.3
      months, not 6. Raise it.
- [ ] $1,289 seeded + $2,000/month → full in about **6.5 months**, so ~April 2027.
- [ ] Hold it in savings. **Not** in stocks, not in the brokerage account.
- [ ] Cost of waiting, measured: delaying investing 6 months costs ~5% of the 20-year
      outcome. Pay it. Being forced to sell stocks in a bad month to cover rent is the
      realistic way to lose real money here.

### Phase 2 — fill the Roth, months 8–19
- [ ] **$583/month → Roth IRA** ($7,000/yr). One broad index fund. VTI is fine.
- [ ] **$1,417/month → taxable brokerage.** Same fund.
- [ ] Verify the 2026 and 2027 limits when you open the account — the $7,000 in Apex Home
      may be a year behind.

### Phase 3 — once the Roth has real money in it
- [ ] Run the 20-name momentum book **inside the Roth only.** Measured 18.6%/yr and it beat
      the market in both halves of the test when tax was zero. In the taxable account the
      same strategy measured **worse than doing nothing** on a risk-adjusted basis. See
      FINDINGS.md, 2026-09-28.
- [ ] Never in the taxable account. The tax on 6.6x annual turnover ate $2,818 in the test
      — 8.5x the trading costs.

### The thing that actually decides whether age 30 is reachable
- [ ] **Work the income side.** $2,000/mo → $1M at 39. $7,024/mo → $1M at 30. Nothing else
      moves that date.
- [ ] You have ~40 apps built across two Lovable workspaces. **Smash Connect** (court
      booking SaaS), **Tennis Match Finder** (league management), and **Winter Tennis
      Clinics** (lead capture, already collecting signups) are products, not projects.
      Coaching income is capped by hours in a day; software is not.
- [ ] This is where the leverage is, and this project has spent six weeks on the 2%
      instead of the 98%.

---

## Corrections owed to the Apex Home app

Found while reading it — **not changed**, flagged for your decision:

1. **The emergency goal contradicts the profile.** `profiles.emergency_months = 6` implies
   $14,130; the Phase 1 goal says $3,000. One of the two is wrong and it should be $14,130.
2. **One-off transactions are logged as recurring expenses.** Of the $2,948.74 of "active
   expenses", about **$594** is single transactions — Zelle payments to Gustavo Rivas and
   Luciana Galves, individual Metro-North tickets, one-time credit-card payments. They
   inflate your monthly burn by 25% and therefore inflate every projection the app makes.
   True fixed burn is **$2,355**.
3. **Five expenses are named "New expense"** ($200, $150, $100, $70, $0) — $520/month
   unlabelled. Worth naming so the categories mean something.
4. **`tax_reserve_pct` is 20% and the app is built around self-employment tax reserves**,
   but you are W-2. Either the app was built for a 1099 situation that has changed, or
   there is private-lesson income it was tracking. Worth reconciling.
5. **Income is essentially unrecorded**: 2 income entries, $193 in 90 days, and
   `income_sources` is empty. The app cannot project anything real until income goes in.
