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

## Apex Home — repaired 2026-09-29

Applied with the principal's explicit permission. Net worth was **overstated in one place
and understated in another**; corrected it is **$10,202.49**, not $8,620.

| # | fault | fix |
|---|---|---|
| 1 | Phase 1 goal was $3,000 (1.3 months) while `profiles.emergency_months = 6` | goal and emergency bucket target both set to **$14,130** (6 × $2,355) |
| 2 | **A credit card counted as a positive asset.** AMEX Platinum, `kind='credit'`, balance +$210, included in net worth — the app sums balances without negating liabilities, so a debt was inflating net worth | balance set to **−$210**, so it subtracts. Net worth effect: **−$420** |
| 3 | Robinhood Agentic recorded at $195; the live broker figure is $2,197.49 | set to **$2,197.49** and `confirmed_at` stamped. Net worth effect: **+$2,002.49** |
| 4 | 14 one-off transactions logged as *recurring* expenses, inflating monthly burn by 25% | deactivated (rows preserved, not deleted). **$593.74** removed; burn now **$2,355.00** |
| 5 | 5 expenses named "New expense" ($520/mo unlabelled) | renamed `UNNAMED - <category> (rename me)` — I will not invent what they are |
| 6 | Primary goal showed $8,220 | synced to $10,202.49, plus a September 2026 net-worth snapshot |

### Two things I flagged that were WRONG, and one claim I have to retract

**`tax_reserve_pct = 20%` is correct and I left it alone.** I called it a mistake because
you are W-2. The income table shows two Venmo payments from Len Spanier tagged
`employment_type: 1099` — so you *do* have self-employment income, and the reserve belongs
there. The app was right and I was wrong.

**That also means I owe you a correction on something bigger.** I told you a Solo 401(k) or
SEP-IRA was off the table because you are W-2. With 1099 income, it is not. Those plans
shelter up to ~25% of net self-employment earnings — far beyond the $7,000 IRA limit. On
the ~$193 currently recorded it is worth nothing yet, but **if the private-lesson side
grows, it becomes the single largest tax shelter available to you.** Worth raising with the
CPA.

### Still unfixable without you

**Income is essentially unrecorded** — 2 entries, $193 in 90 days, `income_sources` empty,
and one lesson rate ("All lessons", $50/hr, W2, Kingshighway). I will not invent income
figures. Until real income goes in, the app cannot project anything, and neither can I.
Also unverified: `Robinhood Investing Personal` at $5,800 — plausibly your two non-agentic
Robinhood accounts, which I cannot read, so I left it untouched.

---

## 2026-10-08 — Schwab Roth opened. Two corrections to this plan.

### 1. The 2026 limit is $7,500, not $7,000

Every earlier version of this plan, and the Morning Market Monitor's standing
brief, said "~$7,000". That was the 2025 figure. For 2026 the base IRA limit is
**$7,500** (catch-up $1,100, not applicable at 22). Single-filer phase-out
$153,000–$168,000, far above his income.

Sourced from Vanguard and several advisory firms citing **IRS Notice 2025-67**;
irs.gov itself confirmed only the $1,100 catch-up. Treat $7,500 as reliable but
confirm against the notice before filing.

Deadline for a 2026-tax-year contribution: **2027-04-15**.

### 2. The emergency fund and the Roth are NOT competing for the same dollars

This plan has ranked them 1 and 2 as if funding one delays the other. That is
mostly wrong and it has cost him a month of contribution room.

**Regular Roth contributions can be withdrawn at any time, for any reason, tax-
and penalty-free.** Only *earnings* are locked (5-year rule + 59½), and IRS
ordering rules take contributions out first. So money contributed as the
emergency fund stays available as the emergency fund — while occupying
contribution room that otherwise expires forever on 2027-04-15.

The condition: the emergency portion must be held in a **money market fund
inside the Roth**, not in equities. Equities would mean selling at a loss in
precisely the scenario the fund exists for. Schwab's default cash sweep pays
materially less than its money market funds — leaving it swept is a silent cost
and the single most common way this gets done wrong.

### What this makes the right move

Capacity: $4,841.24 settled + $2,500 arriving = **$7,341.24**, against a $7,500
limit — $158.76 short of a full max, which the next paycheck covers.

| | |
|---|---|
| contribute for tax year 2026 | up to $7,500 |
| hold in a money market fund inside the Roth | ~$4,500 (the emergency fund) |
| invest in equities inside the Roth | the remainder |
| taxable account | leave alone; no new tech |

$7,500 compounding for 43 years to age 65 is **~$61,000 at 5% real, ~$137,500 at
7% real**, tax-free. Those are illustrative real-return assumptions, not
forecasts — but the asymmetry against leaving the room unused is the point.

### Still true

The $1M-by-30 gap remains an INCOME problem. $7,500 of contribution room does
not change that and no investment solution will. The rate structure
(`knowledge/process/rate-negotiation-case.md`) is the lever.
