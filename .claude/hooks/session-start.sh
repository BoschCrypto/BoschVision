#!/bin/bash
# Prepare a freshly-cloned container so this session can be trusted.
#
# Two jobs. The first was always here: install the package so `hf-bot` and
# pytest work. The second was added 2026-10-20 after the container was rebuilt
# on a STALE clone -- it came up 49 commits behind origin, carrying 46 commits
# of unrelated work that had never been pushed, and nearly had the live trading
# ledger reconciled against a copy missing four real fills. That was caught by
# hand. It should not depend on being caught by hand.
#
# This hook REPORTS drift; it does not fix it. Moving a branch is a judgement
# call -- the stale clone held commits that existed nowhere else -- and a hook
# that silently reset the working tree could destroy work. Loud, not clever.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

python -m pip install --quiet --editable ".[dev]" >&2
echo "hf-trading-bot installed — hf-bot CLI and pytest are ready." >&2

# --- is this checkout current? ------------------------------------------------
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
  echo "WARNING: not on a named branch — verify the checkout before trusting any data file." >&2
  exit 0
fi

if ! git fetch --quiet origin "$branch" 2>/dev/null; then
  echo "WARNING: could not fetch origin/$branch. This checkout may be stale;" >&2
  echo "         verify it before reconciling a ledger or placing an order." >&2
  exit 0
fi

counts="$(git rev-list --left-right --count "HEAD...origin/$branch" 2>/dev/null || echo '')"
ahead="${counts%%	*}"; behind="${counts##*	}"

if [ "${behind:-0}" -gt 0 ] && [ "${ahead:-0}" -gt 0 ]; then
  echo "STOP: $branch has DIVERGED from origin — $ahead local commit(s) origin lacks," >&2
  echo "      $behind remote commit(s) this checkout lacks. The data files here are NOT" >&2
  echo "      the current ones. Preserve the local line on a branch before moving, and" >&2
  echo "      do not reconcile a ledger or place an order until this is resolved." >&2
elif [ "${behind:-0}" -gt 0 ]; then
  echo "STOP: $branch is $behind commit(s) BEHIND origin. data/signals/*.jsonl and the" >&2
  echo "      price store are stale. Run: git pull --ff-only origin $branch" >&2
  echo "      before reconciling a ledger or placing an order." >&2
elif [ "${ahead:-0}" -gt 0 ]; then
  echo "note: $branch is $ahead commit(s) ahead of origin — unpushed work is present." >&2
else
  echo "checkout is level with origin/$branch." >&2
fi
