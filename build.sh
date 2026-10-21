#!/usr/bin/env bash
# Build a single-file Linux executable: dist/ytd-linux-x86_64
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install --quiet --upgrade pyinstaller -r requirements.txt
python3 -m PyInstaller --noconfirm --clean --onefile --console \
    --name ytd-linux-x86_64 \
    --collect-all yt_dlp --collect-all textual --hidden-import ytd_tui \
    yt_downloader.py
echo "Built: dist/ytd-linux-x86_64"
