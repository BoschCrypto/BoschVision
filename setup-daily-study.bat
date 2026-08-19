@echo off
REM ============================================================
REM  Double-click this ONCE to schedule the daily study cycle.
REM  It creates a Windows task, "VANTRIX Daily Study", that runs
REM  daily-study.bat every day at 9:00 AM — growing your agents'
REM  library on your cheap model, no Claude tokens.
REM
REM  To change the time: edit the 09:00 below to any HH:MM (24h)
REM  and double-click this file again.
REM  To remove it later:  schtasks /Delete /TN "VANTRIX Daily Study" /F
REM ============================================================
cd /d "%~dp0"

schtasks /Create /TN "VANTRIX Daily Study" /TR "\"%~dp0daily-study.bat\"" /SC DAILY /ST 09:00 /F

echo.
if %errorlevel%==0 (
  echo Done. "VANTRIX Daily Study" will run every day at 9:00 AM
  echo   as long as your PC is on and you are logged in.
  echo Results append to study-cycle.log, and your dashboard's
  echo Knowledge panel will tick up after each run.
) else (
  echo Could not create the task. Try right-clicking this file and
  echo choosing "Run as administrator".
)
echo.
pause
