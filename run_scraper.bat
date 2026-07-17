@echo off
REM Step 1: Check if Python is installed
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Python not found. Please install Python 3.10+ and re-run.
    pause
    exit /b
)


echo Running the crawler script...
python franklin_scraper.py

REM Done
echo ============================================
echo Script finished successfully!
echo ============================================
pause
