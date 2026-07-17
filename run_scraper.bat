@echo off
REM Step 1: Check if Python is installed
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo Python not found. Please install Python 3.10+ and re-run.
    pause
    exit /b
)

REM Step 2: Move to the project folder so relative paths (config\, logs\) resolve
REM correctly -- required because Task Scheduler starts in C:\Windows\System32
REM unless a working directory is set some other way.
cd /d "C:\Users\Administrator\Desktop\franklin_foreclosure_scraper"
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to cd to project folder.
    exit /b 1
)

REM Step 3: Make sure the logs folder exists
if not exist logs mkdir logs

echo Running the crawler script...
REM -u = unbuffered stdout/stderr, so log lines show up in the file in real
REM time instead of sitting in a buffer until the process exits.
python -u franklin_scraper.py >> logs\run_log.txt 2>&1

REM Done
echo ============================================
echo Script finished (exit code %ERRORLEVEL%)
echo ============================================
