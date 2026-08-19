@echo off
REM ============================================================
REM  The daily study job itself. You don't run this by hand —
REM  "VANTRIX Daily Study" (Windows Task Scheduler) runs it for
REM  you every day. It grows the committee's library on your
REM  cheap NVIDIA model: zero Claude tokens, no prompts to stall.
REM  Output is appended to study-cycle.log next to this file.
REM ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo. >> study-cycle.log
echo ==== %date% %time% : daily study cycle ==== >> study-cycle.log
hf-bot models study-cycle --rounds 3 --tier cheap >> study-cycle.log 2>&1
