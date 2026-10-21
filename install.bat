@echo off
chcp 65001 >nul
title YT-DLP Pro — One-Click Installer
cd /d "%~dp0"

echo.
echo  =====================================================
echo    YT-DLP Pro - One-Click Installer
echo  =====================================================
echo.

REM ─── 1) Check winget ───
where winget >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] winget not found.
    echo  Install "App Installer" from the Microsoft Store, then run this again.
    pause
    exit /b 1
)

REM ─── 2) Install Python if missing ───
where python >nul 2>&1
if errorlevel 1 (
    echo  [1/5] Installing Python ...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo  Python was just installed. Close this window and run install.bat again
    echo  so Windows can pick up the new PATH.
    pause
    exit /b 0
) else (
    echo  [1/5] Python found. OK
)

REM ─── 3) Install FFmpeg if missing (needed for HD / MP3 / GIF) ───
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  [2/5] Installing FFmpeg ...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
) else (
    echo  [2/5] FFmpeg found. OK
)
REM Deno: JavaScript runtime that yt-dlp needs to download full-quality YouTube formats
where deno >nul 2>&1
if errorlevel 1 (
    echo  [2/5] Installing Deno ^(YouTube JavaScript runtime^) ...
    winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
)

REM ─── 4) Virtual environment + libraries ───
echo  [3/5] Installing libraries (yt-dlp + rich + textual + pyperclip) ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo  [ERROR] Library installation failed. Check your internet connection.
    pause
    exit /b 1
)

REM ─── 5) Global command: type YT-DLP in ANY terminal (CMD, PowerShell, Windows Terminal) ───
echo  [4/5] Registering the YT-DLP command ...
set "YTD_DIR=%LOCALAPPDATA%\Microsoft\WindowsApps"
set "YTD_CMD=%YTD_DIR%\YT-DLP.cmd"
if not exist "%YTD_DIR%\" mkdir "%YTD_DIR%" >nul 2>&1
> "%YTD_CMD%" (
    echo @echo off
    echo call "%~dp0YT-DLP-Pro.bat" %%*
)
if not exist "%YTD_CMD%" goto :no_cmd

REM Check which YT-DLP Windows finds first. A pip-installed yt-dlp.exe could shadow ours.
set "YTD_FIRST="
where YT-DLP > "%TEMP%\ytd_where.txt" 2>nul
for /f "usebackq delims=" %%P in ("%TEMP%\ytd_where.txt") do if not defined YTD_FIRST set "YTD_FIRST=%%P"
del "%TEMP%\ytd_where.txt" >nul 2>&1

echo        Done. Open a NEW terminal (CMD, PowerShell or Windows Terminal) and type:  YT-DLP
if defined YTD_FIRST if /i not "%YTD_FIRST%"=="%YTD_CMD%" (
    echo.
    echo  [WARN] Another program named YT-DLP is found first:
    echo         "%YTD_FIRST%"
    echo         Remove or rename it ^(for example a yt-dlp.exe installed with pip^),
    echo         or start the app with YT-DLP-Pro.bat or the desktop shortcut.
)
goto :step6

:no_cmd
echo  [WARN] Could not register the YT-DLP command. Use YT-DLP-Pro.bat instead.

:step6
REM ─── 6) Desktop shortcut ───
echo  [5/5] Creating desktop shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'YT-DLP Pro.lnk')); $sc.TargetPath='%~dp0YT-DLP-Pro.bat'; $sc.WorkingDirectory='%~dp0'; $sc.Description='YT-DLP Pro - YouTube Downloader'; $sc.Save()"

echo.
echo  =====================================================
echo    Installation complete!
echo.
echo    How to run:
echo      1) The "YT-DLP Pro" icon on your desktop
echo      2) Double-click YT-DLP-Pro.bat
echo      3) Terminal:  YT-DLP-Pro.bat ^<YouTube link^>
echo  =====================================================
echo.
pause
