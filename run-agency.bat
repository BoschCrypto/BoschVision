@echo off
REM ============================================================
REM  The Agency - website + HQ + six agents, around the clock.
REM
REM  Double-click this. The first time it sets everything up by
REM  itself (a few minutes). After that it starts in seconds.
REM
REM  BEACON (marketing), FLARE (ads), SENTRY (security),
REM  PRISM (analytics), SCOUT (sales) and FORGE (delivery) keep
REM  working while this window stays open and the PC stays awake.
REM  Anything that would leave the business - posts, ads, emails,
REM  proposals - waits in HQ for your approval.
REM
REM  Details: agency\README.md
REM ============================================================
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment in "%~dp0.venv" ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Python isn't installed yet. Download it from https://www.python.org/downloads/
  echo When the installer opens, tick "Add python.exe to PATH" before clicking Install.
  echo Then double-click this file again.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import anthropic, click, dotenv, yaml" >nul 2>nul
if errorlevel 1 (
  echo Installing the agency. The first time this downloads about 270 MB and takes a few minutes...
  ".venv\Scripts\python.exe" -m pip install -e ".[agency]"
  if errorlevel 1 (
    echo.
    echo Installation failed - see the messages above. Check your internet connection and try again.
    echo.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m agency.cli hq-token | clip
echo.
echo  Your HQ login token is copied to the clipboard.
echo  When the login page opens in your browser, click the box and press Ctrl+V.
echo.
echo  Keep this window open. To stop the agency, press Ctrl+C, then type Y.
echo.
".venv\Scripts\python.exe" -m agency.cli up --open
echo.
echo The agency has stopped.
pause
