@echo off
REM ============================================================
REM  Live Agent Cortex - REMOTE dashboard (reach it anywhere)
REM  Starts the dashboard with a secret token AND a public
REM  HTTPS tunnel. Watch this window for a line like:
REM     PUBLIC LINK (open on any device): https://....trycloudflare.com/?key=...
REM  Open that link on your phone or any browser.
REM
REM  Uses ZERO Claude tokens: the runner is deliberately OFF, so
REM  commands only QUEUE. A review spends tokens solely when YOU
REM  run it in Claude Code. Keep this window open; Ctrl+C to stop.
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

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo cloudflared is not installed. Install it once, then re-run this file:
  echo    winget install cloudflare.cloudflared
  echo.
  echo Starting LOCAL-only for now ^(no public link^)...
  hf-bot dashboard --auth --open
  pause
  exit /b 0
)

echo Starting the dashboard with a token and a public tunnel...
echo Look below for the PUBLIC LINK, then open it on your phone.
hf-bot dashboard --auth --tunnel --open
pause
