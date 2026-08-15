#!/bin/bash
# Install the hf-trading-bot package so `hf-bot` and the test suite work in
# Claude Code on the web / Routine sessions (which clone the repo fresh).
# Local sessions already have their own setup, so this only runs remotely.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Editable install with the dev extra (pytest). Idempotent and cache-friendly.
python -m pip install --quiet --editable ".[dev]" >&2

echo "hf-trading-bot installed — hf-bot CLI and pytest are ready." >&2
