@echo off
title Amazon Scraper

cd /d "%~dp0"

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
)

start pythonw gui.py
