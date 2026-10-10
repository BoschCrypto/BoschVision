@echo off
REM ============================================================
REM  The Agency - website + HQ + six agents, around the clock.
REM
REM  BEACON (marketing), FLARE (ads), SENTRY (security),
REM  PRISM (analytics), SCOUT (sales) and FORGE (delivery) keep
REM  working while this window stays open and the PC stays awake.
REM  Anything that would leave the business - posts, ads, emails,
REM  proposals - waits in HQ for your approval.
REM
REM  MODEL: set ANTHROPIC_API_KEY in .env, or be logged in to
REM  Claude Code. With neither, the writing agents use templates.
REM  Spend is capped by AGENCY_MAX_LLM_CALLS_PER_DAY (default 40).
REM
REM  HQ opens in your browser; the login token is printed below.
REM  Details: agency\README.md
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo No .venv found. Create it first:  python -m venv .venv
  echo Then install:  .venv\Scripts\pip install -e ".[agency]"
  pause
  exit /b 1
)

where agency >nul 2>nul
if errorlevel 1 (
  echo Installing the agency into .venv ...
  pip install -q -e ".[agency]"
)

echo Starting the agency. Keep this window open; Ctrl+C stops everything.
agency up --open
pause
