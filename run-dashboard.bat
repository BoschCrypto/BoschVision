@echo off
REM ============================================================
REM  Live Agent Cortex - LOCAL dashboard (this machine only)
REM  Uses ZERO Claude tokens: it only serves the HUD and queues
REM  commands. A review spends tokens only when YOU run it in
REM  Claude Code. Keep this window open; Ctrl+C to stop.
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo No .venv found. Create it first:  python -m venv .venv
  echo Then install:  .venv\Scripts\pip install -e .
  pause
  exit /b 1
)

echo Starting the Live Agent Cortex - your browser will open at http://127.0.0.1:8420
echo Keep this window open. Press Ctrl+C here to stop.
hf-bot dashboard --open
pause
