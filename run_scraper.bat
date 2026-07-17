@echo off
cd /d "C:\Users\Administrator\Desktop\franklin_foreclosure_scraper"
if errorlevel 1 (
    echo Failed to cd to project folder
    exit /b 1
)
if not exist logs mkdir logs
python franklin_scraper.py >> logs\run_log.txt 2>&1
