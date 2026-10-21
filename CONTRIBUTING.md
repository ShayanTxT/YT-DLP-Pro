# Contributing

Thanks for your interest in improving YT-DLP Pro! ❤️

## Development setup

```bash
git clone https://github.com/ShayanTxT/ytd-pro.git
cd ytd-pro
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python yt_downloader.py       # interactive menu
python yt_downloader.py --help
```

You also need [FFmpeg](https://ffmpeg.org/) on your PATH
(`winget install Gyan.FFmpeg` on Windows).

## Running tests

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q tests
```

Linux developers can use `./install.sh` for the same environment and `./build.sh` to build the binary.

## Refreshing screenshots

The README images are generated from the real app:

```bash
python scripts/capture_screenshots.py
```

It uses a temporary folder with demo data, so your own settings and downloads are untouched.

## Guidelines

- Core logic lives in `yt_downloader.py`; the full-screen UI lives in `ytd_tui.py`
  (Textual). Keep UI code out of the core, and route status messages through `say()`
  so both the classic console and the TUI work.
- All yt-dlp options go through `Downloader._build_opts()` so every code path
  (CLI, menu, watch mode, sync) behaves consistently.
- Match the existing style: type hints, short docstrings, English UI strings.
- Test at least `--help`, the interactive menu, and one real download
  before opening a PR.

## Reporting bugs

YouTube changes constantly — **before filing a bug, run `ytd --update`**.
If the problem persists, open an issue with:

1. The exact command you ran
2. The full error message
3. Your `ytd --version` output and Windows version

## Pull requests

Small, focused PRs are easiest to review. If you're adding a feature,
please also update `README.md` and `CHANGELOG.md`.
