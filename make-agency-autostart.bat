@echo off
REM ============================================================
REM  Double-click this ONCE. From then on the agency starts by
REM  itself every time you sign in to Windows, so the agents keep
REM  working after a restart or a Windows Update.
REM
REM  It puts an "Agency" shortcut in your Startup folder (no admin
REM  rights needed). To undo: press Win+R, type shell:startup,
REM  press Enter and delete the "Agency" shortcut.
REM ============================================================
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\Agency.lnk');" ^
  "$lnk.TargetPath = '%~dp0run-agency.bat';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = '%SystemRoot%\System32\SHELL32.dll, 13';" ^
  "$lnk.Description = 'Start the agency: website, HQ and six agents';" ^
  "$lnk.Save();"

echo.
echo Done. The agency will now start each time you sign in to Windows.
echo Keep the PC from sleeping: Settings ^> System ^> Power ^> Screen and sleep ^>
echo "When plugged in, put my device to sleep after" ^> Never.
echo.
pause
