@echo off
REM Windows batch script to run daily portfolio generation and submission
REM This script should be scheduled via Windows Task Scheduler

cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python scripts\daily_submit.py

REM Log the exit code
if %ERRORLEVEL% EQU 0 (
    echo [%date% %time%] Daily portfolio generation completed successfully >> logs\daily_submit.log
) else (
    echo [%date% %time%] Daily portfolio generation failed with exit code %ERRORLEVEL% >> logs\daily_submit.log
)

exit /b %ERRORLEVEL%

