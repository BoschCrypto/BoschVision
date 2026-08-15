# Position sizing and Kelly — the bet size ends accounts, not the direction

*Studied by: risk-manager (BASTION). Core seed note; expand via
`hf-bot study next --agent risk-manager`.*

## Key principle

You can be right about direction and still be ruined by size. Position sizing —
not selection — is what most often blows up accounts, because losses compound
geometrically: the math of ruin is unforgiving and asymmetric. The job is to
size each bet so that being wrong (even repeatedly) is survivable, and to cap
total risk so no single outcome is fatal.

## Concrete anchors

- **Kelly criterion.** For a known edge, Kelly gives the growth-optimal bet
  fraction; but it assumes you *know* the edge, and full Kelly produces
  stomach-churning drawdowns. Practitioners use **fractional (half or
  quarter) Kelly** because edges are estimated with error and overbetting is
  far more destructive than underbetting.
- **Risk of ruin.** Even a positive-expectation system has a nonzero
  probability of a losing streak that wipes you out if bets are too large.
  Sizing must keep that probability negligible, not merely low.
- **Drawdown asymmetry.** A 50% loss requires a 100% gain to recover; a 20%
  loss needs 25%. This asymmetry is why capping downside dominates chasing
  upside.

## What this changes about how I (BASTION) operate

- I size from downside first: what is the loss if the thesis is simply wrong,
  and is the whole book survivable if several positions are wrong together?
- I treat full-Kelly and "conviction sizing" as red flags; I default to
  fractional sizing because our edge is always estimated, never known.
- I have veto authority over size and use it — a great idea sized to threaten
  the account is a bad risk decision.

## Sources

J.L. Kelly Jr. (1956) original paper; William Poundstone, *Fortune's Formula*;
Edward Thorp on fractional Kelly in practice. Public frameworks; no proprietary
data.
