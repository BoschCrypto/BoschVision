# Apex futures system — GO-LIVE CRITERIA (pre-registered 2026-11-10)

Written **before** the 2026-12-23 review and before any real money is in
reach, which is the only time a rule like this is worth anything. Waldo asked
for it on 2026-11-10, the day the walk-forward came back positive — i.e. at the
moment the evidence improved and the temptation with it.

**The rule about the rule:** these criteria may be revised, but only in
writing, with the reason and the date, and never during or immediately after a
run of wins or losses. A criterion loosened the week it is about to be breached
is not a criterion.

---

## THE SIZING FLOOR — measured 2026-11-10, and it binds everything below

Replaying all five years at different account sizes with the live settings:

| account | trades taken (of 332) | skipped: size rounded to 0 | median contracts |
|---:|---:|---:|---:|
| $1,000 | 297 | **35** | 1 |
| $2,000 | 324 | 8 | 2 |
| $3,000 | 331 | 1 | 3 |
| **$5,000** | **332** | **0** | 6 |
| $10,000 | 332 | 0 | 11 |

**Below about $5,000 the system cannot execute its own rules.** One nano BTC
contract is ~$750 of notional at current prices, so a small account rounds
positions to 0 or 1 and the 2%-risk sizing stops meaning anything. The higher
headline return at $1,000 (+403% vs +350%) is a rounding artifact from lumpier
bets, not an edge — it is variance, and it cuts both ways.

**Minimum viable allocation: $5,000.** Not a target, a floor.

---

## GATE A — preconditions that have nothing to do with the strategy

All three must be true. None is about whether the system works.

- [ ] **Emergency fund at $7,065** (3 x $2,355) held in a government money
      market fund, not a sweep, not equities. Currently **$0**.
- [ ] **2026 Roth funded, or the 2027-04-15 deadline consciously allowed to
      pass in writing.** $7,500 of room; one skipped year costs about $128,582
      at 65 in today's dollars at 7% real, and cannot be back-filled.
- [ ] **Schwab balance reported** so every limit below is measured against the
      whole book rather than the Robinhood fragment. No connector here reaches
      Schwab; this number has to come from Waldo by hand.

Rationale: he is 23, rent is $1,835 of a $2,355 monthly burn, and the system's
planning drawdown is −30% to −40%. Money that might be needed for rent cannot
be in a leveraged futures account. This gate is not negotiable and is not a
judgement about the strategy.

---

## GATE B — evidence

- [ ] **At least 30 closed trades** in the simulator. At the measured rate of
      ~66 trades/year that is roughly **April 2027**, not December 2026.
- [ ] Realised **profit factor >= 1.3** over those trades. (Five-year backtest:
      2.13. Stressed with doubled fees + slippage: 1.53. 1.3 is a floor.)
- [ ] **Replay gate passing continuously** — live execution still matching a
      replay of the frozen rules on stored bars.
- [ ] **No unexplained divergence** between the simulator and the independent
      Python port in `scripts/apex/`.

**What 30 trades can and cannot tell you.** It can detect a broken system: if
the profit factor is under 1.0 after 30 trades, something is wrong with the
execution, the costs or the premise. It **cannot** confirm a +30%/yr edge. Ten
of 332 backtest trades produced 87% of the profit, so a 30-trade sample may
contain none of the trades that matter. Statistical confirmation of the edge
itself takes years and is not available before this decision has to be made.

**The 2026-12-23 review will have about 10 closed trades.** That is a machinery
check — did it run, did it fill, did the replay match — and explicitly not a
verdict on profitability. Do not treat it as one.

---

## GATE C — execution readiness (none of this is tested yet)

- [ ] Venue chosen and account open; confirm it lists the contract sizes the
      system assumes (0.01 BTC, 0.1 ETH).
- [ ] Margin requirements understood, including what happens to a 3x-notional
      position in a gap.
- [ ] **Tax treatment verified with a professional before the first trade.**
      US futures may fall under Section 1256 with mark-to-market at year end,
      which is very different from spot crypto. This is NOT settled here and
      must not be assumed.
- [ ] **Slippage measured on at least 5 real fills** against the modelled
      price. The backtest fills stops at the stop price unless the bar gaps;
      reality will be worse. If measured slippage exceeds 0.25% per fill, the
      planning band drops below +22%/yr and this document is reopened.

---

## GATE D — the one that is about Waldo, not the system

- [ ] **Written answer, before funding, to: "what will I do during a 266-day
      stretch below a prior peak?"** That is the measured worst case over five
      years, and the win rate is 26% — roughly three losses for every win.

This gate exists because of evidence in this project, not as a platitude: the
record shows a preference for acting (asking why buy orders had not been run,
following a daily signal at a measured ~169%/yr of drag). A trend system pays
its holder in long stretches of nothing. If the honest answer is "I would
interfere," the system should not be funded, and that is a legitimate outcome.

---

## THE CAPITAL RULE, and the arithmetic that currently blocks it

Allocation must be: **>= $5,000** (sizing floor), from **taxable surplus only**,
money that can go to zero without changing anything about his life.

**Never** from: the emergency fund; Roth contribution room (a −35% drawdown
inside a Roth permanently destroys irreplaceable tax-advantaged space);
borrowed money; or money needed within 3 years.

Against his own concentration caps (`risk-limits.md`, TOTAL = book − $7,065):

| cap on TOTAL | book required to fund $5,000 |
|---|---:|
| 10% (his third-party-mandate cap) | **$57,065** |
| 20% (aggressive for a leveraged system) | $32,065 |

Visible Robinhood book on 2026-11-03: **$3,601.89**, plus an unreported Schwab
balance.

**State this plainly: the minimum size at which the system executes correctly
is far above what his own risk limits currently permit him to allocate.** The
strategy being good does not mean it can be funded. Unless Schwab holds a large
balance, this is a 2028-or-later decision at the current savings rate, and the
honest answer in the meantime is to keep it on paper. That is not a failure of
the strategy; it is the correct order of operations for a 23-year-old with no
emergency fund.

---

## KILL CRITERIA — pre-committed, no discretion at the moment of pain

Stop or cut **without re-litigating** when any of these fires:

1. **−25% from high-water mark** -> halve risk per trade. (Existing rule.)
2. **−35% from high-water mark** -> flat, stop, full review. (Existing rule.)
3. **Profit factor < 1.0 after 30 closed trades** -> stop, reopen this document.
4. **Replay gate fails twice consecutively** -> stop until the cause is found.
   The gate failing means live execution no longer matches the frozen rules.
5. **Any manual override of a system entry or exit** -> stop for 30 days. An
   overridden system is discretionary trading wearing a system's clothes, and
   none of the evidence above applies to it.
6. **400 days below a prior peak** -> stop and review. Worse than the measured
   266-day worst case; at that point the regime assumption is in question.

---

## WHAT THIS DOCUMENT DOES NOT AUTHORIZE

- The **Alpaca equity paper automation**. Different system, four defects found
  2026-11-10, three still unfixed. Its evidence base is separately flawed
  (profit factors of 5–17, no cost model, 49 symbols chosen before testing).
- **Following Prof. Adam's RSPS signal**. Measured at ~169%/yr of implied drag;
  the sleeve finished $55.25 behind never having traded. Halted.
- Any **discretionary trade** reasoned from the system's output.

---

## REVIEW SCHEDULE

| date | ~closed trades | what it decides |
|---|---:|---|
| 2026-12-23 | ~10 | Machinery only: did it run, fill, and replay cleanly. No verdict. |
| ~2027-04 | ~30 | Gate B assessed. Earliest possible funding date, and only if Gates A, C and D are also met. |
| annually | — | Re-run `walk_forward.py` with another year of data. |

**Signed off by:** Waldo Bosch, ________________  date ________

Unsigned, this is a draft and nothing is authorized by it.
