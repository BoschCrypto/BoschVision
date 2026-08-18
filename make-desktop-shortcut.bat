@echo off
REM ============================================================
REM  Double-click this ONCE. It puts a "VANTRIX Dashboard" icon
REM  on your Desktop that launches the dashboard (agents + tiered
REM  models) with one click — no typing, no PowerShell, ever again.
REM ============================================================
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\VANTRIX Dashboard.lnk');" ^
  "$lnk.TargetPath = '%~dp0run-agents.bat';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = '%SystemRoot%\System32\SHELL32.dll, 13';" ^
  "$lnk.Description = 'Launch the VANTRIX Live Agent Cortex dashboard';" ^
  "$lnk.Save();"

echo.
echo Done. A "VANTRIX Dashboard" icon is now on your Desktop.
echo Double-click it any time to open the dashboard.
echo.
pause
