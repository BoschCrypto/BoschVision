@echo off
REM Double-click to launch the Live Agent Cortex dashboard in your browser.
REM Keep this window open while you use the dashboard; close it (or Ctrl+C) to stop.
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo No .venv found. Create it first:  python -m venv .venv
  echo Then install:  .venv\Scripts\pip install -e .
  pause
  exit /b 1
)

echo Starting the Live Agent Cortex — your browser will open at http://127.0.0.1:8420
echo Keep this window open. Press Ctrl+C here to stop the dashboard.
hf-bot dashboard --open
pause
