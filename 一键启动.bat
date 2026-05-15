@echo off
title Amazon Scraper

cd /d "%~dp0"

echo ================================================
echo   Amazon New Product Scraper
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import requests, bs4, lxml, schedule" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install requests beautifulsoup4 lxml schedule -q
    echo [DONE] Dependencies installed.
    echo.
)

echo [START] Scraping... Please do not close this window.
echo [INFO]  Results will be saved in the data\ folder.
echo.

python run_scraper.py

echo.
echo ================================================
echo  Done! Check the data\ folder for CSV files.
echo ================================================
echo.
pause
