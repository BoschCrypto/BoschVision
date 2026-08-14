# Study & recall protocol (shared by every committee agent)

This file documents the learning loop the whole committee follows. It is
referenced by each agent's own definition; the operative instructions ("recall
first", "how to study") are repeated in each agent file so they load with that
subagent.

## Recall first — at the start of EVERY task

Before doing any analysis, load what you already know:

1. Read your role's library directory: `knowledge/<your-agent-name>/` — every
   `.md` there is a topic you have already studied. Skim the ones relevant to
   the task.
2. Recall prior conclusions: `hf-bot memory recall <TICKER or concept>` and, if
   the Agently brain is reachable, `mcp__Agently__search`.

Open your analysis from that base. Do not re-derive what the library already
establishes; build on it, and note when the current case contradicts it.

## How to study a topic (when dispatched to learn)

You are given a curriculum topic (via `hf-bot study next --agent <you>` or a
study cycle). Then:

1. **Research, don't reproduce.** Use your web/news tools to research the
   concepts, frameworks, and *documented history* of the topic. Never
   reproduce copyrighted book text — study the ideas and cite sources.
2. **Distil to a note.** Write `knowledge/<your-agent-name>/<slug>.md` with:
   key principles; 2-3 concrete historical cases; **what this changes about how
   you operate** (the point of studying is to change future behaviour); and
   your sources.
3. **Persist a lesson.** `hf-bot memory persist --kind lesson --title "..." --body "..."`
   (self-contained, dated), then mirror it into the Agently brain with
   `mcp__Agently__remember` if reachable. If Agently is unavailable, that's
   fine — the note and the local memory already hold it.
4. **Record and commit.** `hf-bot study record --agent <you> --topic <topic> --slug <slug> --sources <n>`,
   then commit `knowledge/<you>/<slug>.md`.

One focused topic per dispatch. Honest sourcing always.
