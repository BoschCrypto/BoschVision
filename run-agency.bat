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
  echo If the installer offers "Add python.exe to PATH", tick it before installing.
  echo Then double-click this file again.
  echo.
  pause
  exit /b 1
)

REM PyYAML has no ready-made package for the newest Python on Windows; this
REM lets it install in plain-Python form instead of needing a C compiler.
set PYYAML_FORCE_LIBYAML=0
".venv\Scripts\python.exe" -c "import anthropic, click, dotenv, yaml" >nul 2>nul
if errorlevel 1 (
  echo Installing the agency. The first time this downloads about 270 MB and takes a few minutes...
  ".venv\Scripts\python.exe" -m pip install -e ".[agency]"
  if errorlevel 1 (
    echo.
    echo Installation failed - see the messages above.
    echo Check your internet connection and double-click this file again. If you have
    echo reinstalled or upgraded Python since the first run, delete the .venv folder
    echo next to this file first, so it is set up fresh.
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
if errorlevel 3 (
  echo The agency was already running in another window, so HQ was opened in your browser.
  echo This window can be closed.
) else if errorlevel 1 (
  echo The agency did not start - see the message above.
) else (
  echo The agency has stopped.
)
echo.
pause
