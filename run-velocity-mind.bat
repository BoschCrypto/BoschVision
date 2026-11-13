@echo off
REM ============================================================
REM  VELOCITY mind - LIVE view of the five trading agents
REM  Paper trading only: no order ever leaves this machine.
REM  Uses ZERO Claude tokens. Follows live Coinbase ETH prices.
REM  Keep this window open; Ctrl+C to stop.
REM
REM  To flatten and stop trading without closing this window,
REM  create an empty file named .velocity_kill in this folder.
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

echo Starting VELOCITY mind - your browser will open at http://127.0.0.1:8765
echo It warms up for about a minute, then goes live. Keep this window open.
velocity mind --open
pause
