@echo off
REM Windows batch script for bi-weekly portfolio run (Sundays, every 2 weeks)
REM Schedule via Task Scheduler with "every 2 weeks on Sunday"
REM Does NOT submit to MAYS; writes trades CSV to run folder; tracks performance separately

cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python scripts\biweekly_run.py

if %ERRORLEVEL% EQU 0 (
    echo [%date% %time%] Biweekly portfolio run completed successfully >> logs\biweekly_run.log
) else (
    echo [%date% %time%] Biweekly portfolio run failed with exit code %ERRORLEVEL% >> logs\biweekly_run.log
)

exit /b %ERRORLEVEL%
