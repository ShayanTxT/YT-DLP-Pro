@echo off
chcp 65001 >nul
title Build YT-DLP-Pro.exe
cd /d "%~dp0"

echo.
echo  =====================================================
echo    Building standalone EXE ^(no Python needed^)
echo  =====================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed! Run install.bat first.
    pause
    exit /b 1
)

echo  [1/2] Installing build tools...
python -m pip install --upgrade pyinstaller -r requirements.txt

echo  [2/2] Building executable... ^(can take a few minutes^)
pyinstaller --noconfirm --clean --onefile --console --name "YT-DLP-Pro" --collect-all yt_dlp --collect-all textual --hidden-import ytd_tui yt_downloader.py

if exist "dist\YT-DLP-Pro.exe" (
    echo.
    echo  SUCCESS:  dist\YT-DLP-Pro.exe
    echo.
    echo  Pro tip: rename it to ytd.exe, drop it in a folder on your PATH
    echo  ^(e.g. C:\Tools^), and from then on any terminal can do:
    echo.
    echo      ytd https://youtu.be/xxxx
    echo      ytd --watch
    echo.
) else (
    echo  [ERROR] Build failed.
)
pause
