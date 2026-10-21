#!/usr/bin/env bash
# YT-DLP Pro — installer for Linux (and macOS, untested).
#   ./install.sh              install into ./.venv and register the `ytd` and `YT-DLP` commands
#   ./install.sh --uninstall  remove the commands, desktop entry and ./.venv
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$HOME/.local/share/applications/ytd-pro.desktop"

green()  { printf '\033[1;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }
die()    { red "x $*"; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    green "Removing YT-DLP Pro ..."
    rm -f "$BIN_DIR/ytd" "$BIN_DIR/YT-DLP" "$DESKTOP_FILE"
    rm -rf "$DIR/.venv"
    green "Done. Your downloads and settings in ~/.ytdpro were kept."
    exit 0
fi

green "YT-DLP Pro installer"

command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3.10+ first."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 3.10 or newer is required (found $(python3 -V 2>&1))."

green "[1/4] Creating virtual environment (.venv)"
if ! python3 -m venv .venv 2>/dev/null; then
    rm -rf .venv
    die "Could not create a virtual environment. On Debian/Ubuntu run: sudo apt install python3-venv"
fi
.venv/bin/python -m pip install --quiet --upgrade pip
green "[2/4] Installing libraries (yt-dlp, rich, textual, pyperclip)"
.venv/bin/python -m pip install --quiet -r requirements.txt

green "[3/4] Registering commands in $BIN_DIR"
mkdir -p "$BIN_DIR"
for name in ytd YT-DLP; do
    cat > "$BIN_DIR/$name" <<LAUNCHER
#!/usr/bin/env bash
exec "$DIR/.venv/bin/python" "$DIR/yt_downloader.py" "\$@"
LAUNCHER
    chmod +x "$BIN_DIR/$name"
done
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) yellow "  $BIN_DIR is not on your PATH. Add this line to ~/.bashrc or ~/.zshrc:"
       echo  "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

green "[4/4] Desktop menu entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<ENTRY
[Desktop Entry]
Type=Application
Name=YT-DLP Pro
Comment=The YouTube downloader that works while you browse
Exec=x-terminal-emulator -e "$BIN_DIR/ytd"
Terminal=false
Categories=AudioVideo;Network;
ENTRY

echo
if ! command -v ffmpeg >/dev/null 2>&1; then
    yellow "ffmpeg is missing (needed for HD merge, MP3, clips and GIF). Install it with:"
    echo   "  sudo apt install ffmpeg        # Debian / Ubuntu / Mint"
    echo   "  sudo dnf install ffmpeg        # Fedora (RPM Fusion)"
    echo   "  sudo pacman -S ffmpeg          # Arch"
fi
if [ -n "${XDG_SESSION_TYPE:-}" ] && [ "${XDG_SESSION_TYPE}" = "wayland" ]; then
    yellow "Clipboard Watch on Wayland needs:  sudo apt install wl-clipboard"
elif ! command -v xclip >/dev/null 2>&1 && ! command -v wl-paste >/dev/null 2>&1; then
    yellow "Clipboard Watch needs xclip (X11) or wl-clipboard (Wayland):  sudo apt install xclip"
fi

green "Installed. Open a new terminal and type:  ytd   (or YT-DLP)"
