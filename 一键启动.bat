@echo off
chcp 65001 >nul
title 亚马逊新品采集器

echo ================================================
echo      亚马逊新品采集器  正在启动...
echo ================================================
echo.

:: 切换到脚本所在目录
cd /d "%~dp0"

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或以上版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查依赖是否安装
python -c "import requests, bs4, lxml, schedule" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装依赖包，请稍候...
    pip install requests beautifulsoup4 lxml schedule -q
    echo [完成] 依赖安装完毕
    echo.
)

echo [开始] 正在采集店铺数据，请勿关闭此窗口...
echo [提示] 采集完成后结果保存在 data\ 文件夹中
echo.

python run_scraper.py

echo.
echo ================================================
echo  采集完成！请查看 data\ 文件夹中的 CSV 文件
echo ================================================
echo.
pause
