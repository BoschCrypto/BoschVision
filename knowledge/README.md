# Team knowledge base

This directory is the committee's growing, role-specific **library** — the
honest version of "the team learns the more it does."

## What "learning" means here (and what it doesn't)

The agents are Claude subagents with fixed roles. They do **not** retrain, and
they do **not** read or reproduce copyrighted books. What they do is
*research* the concepts, frameworks, and documented history in `curriculum.yaml`
using their web/news tools, then distil **durable, sourced lessons** into
`knowledge/<agent>/<topic>.md`. Those notes are then **recalled at the start of
every task**, so the accumulated study actually informs the work.

That's genuine learning-by-accumulation — measurable and reviewable — not a
model that silently "gets smarter." The dashboard says exactly this.

## Layout

- `curriculum.yaml` — per-agent topics: what to study and why it matters to
  that role, with a few seed authors/events/frameworks as research anchors.
- `<agent>/<topic-slug>.md` — one distilled note per studied topic: key
  principles, historical cases, **what it changes about how this agent
  operates**, and sources. Committed to git, so the library is durable and
  survives across machines and sessions (the memory *database* is gitignored;
  these files are the durable source of truth).

## How it grows

```bash
hf-bot study status                      # per-agent library size
hf-bot study next --agent equity-analyst # the next uncovered topic + a brief
# ...a Claude session researches it, writes the note, then:
hf-bot study record --agent equity-analyst --topic value-investing-foundations \
    --slug value-investing-foundations --sources 4
```

The Python CLI cannot research — only a Claude session can. So `study next`
prints a brief; an executor (a Claude session, or `Use the cio agent to run a
study cycle`) does the reading, writes the note here, records a memory episode,
mirrors it to the Agently cross-session brain, and calls `study record`.

Every specialist agent reads its own `knowledge/<agent>/` directory before
starting any task — that recall is what turns a growing library into sharper
operators.

## Growing it from the dashboard

The dashboard's **Knowledge / curriculum** panel has a **STUDY** button on each
agent row and a **STUDY CYCLE** button in the header. Clicking one dispatches a
study run through the dashboard's agent runner — the same executor (and the
same Claude → local-model failover) as the APEX console. Each click confirms
first, because a study run spends tokens.

## Automating it — the opt-in daily Routine (OFF by default)

A cloud **Routine** can run a light study cycle for you every day: it picks the
three least-covered agents, has each study its next curriculum topic, runs the
tests, and commits the new `knowledge/` files to the dev branch. It never
trades — study only.

**This Routine is created DISABLED.** Nothing runs and no tokens are spent until
you explicitly turn it on. This is deliberate: autonomous daily runs cost tokens
each day, so enabling it is your call.

- **Name:** `Committee Study Cycle`
- **Trigger id:** `trig_01LqAYMUe35ifyhr74WFxYMk`
- **Schedule:** `0 8 * * *` (daily, 08:00 UTC) — a light trickle of 3 topics/day
- **Environment:** BoschVision cloud env, fresh session per fire
- **Cost when ON:** roughly one short Claude session per day (three researched
  notes + tests + a commit); **zero while OFF**

**Turn it ON** when you want the committee to keep learning on its own — from the
Routines UI on claude.ai, or by asking Claude to enable trigger
`trig_01LqAYMUe35ifyhr74WFxYMk`. **Turn it OFF** the same way at any time.

To recreate it from scratch (e.g. in a new environment), a session with the
Claude Code Remote tools runs `create_trigger` with the prompt above, then
`update_trigger … enabled:false` to keep it opt-in.
