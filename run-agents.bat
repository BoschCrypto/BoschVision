@echo off
REM ============================================================
REM  Live Agent Cortex - FULL agent mode with tiered models.
REM  Double-click this to open the dashboard and drive everything
REM  from the browser: STUDY buttons and easy console questions run
REM  on your cheap model (NVIDIA nemotron - no Claude tokens); the
REM  hard calls (buy/sell/valuation/committee) run on Claude.
REM  Keep this window open; press Ctrl+C here to stop.
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo Starting the Live Agent Cortex (agents + tiered models)...
echo   - Study buttons / easy questions  -> cheap model, no Claude tokens
echo   - Buy/sell/valuation/committee     -> Claude
echo Your browser will open at http://127.0.0.1:8420
echo Keep this window open. Press Ctrl+C to stop.
hf-bot dashboard --open --enable-agent-runner --tiered
pause
