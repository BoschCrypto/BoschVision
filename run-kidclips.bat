@echo off
REM Keep producing kids' dance videos: 3 per day, uploaded as PRIVATE for review.
REM Remove --upload to only save videos to kidclips_output\.
cd /d "%~dp0"
kidclips loop --per-day 3 --upload
pause
