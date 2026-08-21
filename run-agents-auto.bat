@echo off
REM ============================================================
REM  FULLY AUTOMATIC mode. Agent-proposed orders place themselves
REM  with NO approval click.
REM
REM    * PAPER MONEY ONLY - unattended placement refuses any live
REM      account, with no override.
REM    * Kill switch, position caps, buying power and the PDT
REM      guard all still apply.
REM    * Per-order ceiling below (default $500) - anything larger
REM      stays staged for you to approve by hand.
REM
REM  Raise or lower the ceiling by editing --auto-execute-max.
REM  Use run-agents.bat instead if you want to approve each order.
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo Starting VANTRIX in FULLY AUTOMATIC mode.
echo   Agent orders will PLACE THEMSELVES (paper money, max $500/order).
echo   Kill switch and position caps still apply.
echo   Close this window or press Ctrl+C to stop.
echo.
hf-bot dashboard --open --enable-agent-runner --tiered --committee-model sonnet --auto-execute --auto-execute-max 500
pause
