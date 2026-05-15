@echo off
chcp 65001 >nul
title 亚马逊新品采集器（每天08:00自动运行）

cd /d "%~dp0"

echo ================================================
echo   亚马逊新品采集器 - 每天 08:00 自动采集
echo   保持此窗口开启，勿关闭
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python
    pause
    exit /b 1
)

python -c "import requests, bs4, lxml, schedule" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装依赖...
    pip install requests beautifulsoup4 lxml schedule -q
)

echo [运行中] 等待每天 08:00 自动执行采集...
echo [提示]   结果保存在 data\ 文件夹
echo.

python run_scraper.py --schedule
pause
