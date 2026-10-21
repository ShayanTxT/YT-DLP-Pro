@echo off
chcp 65001 >nul
title YT-DLP Pro — YouTube Downloader
cd /d "%~dp0"

REM ─── Launcher: double-click me, or call from any terminal ───
REM Example:   YT-DLP-Pro.bat https://youtu.be/xxxx -q 720
REM            YT-DLP-Pro.bat --watch

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" yt_downloader.py %*
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [ERROR] Python is not installed! Run install.bat first.
        echo.
        pause
        exit /b 1
    )
    python -c "import yt_dlp, rich" >nul 2>&1
    if errorlevel 1 (
        echo  Installing required libraries... please wait.
        python -m pip install -r requirements.txt
    )
    python yt_downloader.py %*
)

