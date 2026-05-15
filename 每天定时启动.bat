@echo off
title Amazon Scraper - Daily Schedule (08:00)

cd /d "%~dp0"

echo ================================================
echo   Amazon Scraper - Runs daily at 08:00
echo   Keep this window open.
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

python -c "import requests, bs4, lxml, schedule" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install requests beautifulsoup4 lxml schedule -q
)

echo [RUNNING] Waiting for 08:00 to start scraping...
echo [INFO]    Results saved in data\ folder.
echo.

python run_scraper.py --schedule
pause
