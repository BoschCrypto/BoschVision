@echo off
REM ============================================================
REM  APEX Command Center - speak to your committee, anywhere
REM
REM  Starts the dashboard with a token + public tunnel AND the
REM  agent-runner ON. When YOU send a command from the console,
REM  APEX runs the committee immediately and reports back.
REM
REM  TOKENS: every command you send spends tokens (a committee
REM  run) - that is your authorization by issuing the command.
REM  The token gates the link, so ONLY you can command. Nothing
REM  runs unless you send something.
REM
REM  Needs: the `claude` CLI installed + logged in, and
REM  cloudflared for the public link. Keep this window open.
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

where claude >nul 2>nul
if errorlevel 1 (
  echo WARNING: the `claude` CLI was not found. APEX cannot run without it,
  echo so your commands will only QUEUE. Install/log in to Claude Code, then
  echo re-run this file. Continuing in queue-only mode...
  echo.
)

echo Starting the APEX Command Center with a public link...
echo Watch below for the PUBLIC LINK, then open it on any device and command APEX.
hf-bot dashboard --auth --tunnel --open --enable-agent-runner
pause
