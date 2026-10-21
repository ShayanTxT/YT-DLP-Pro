# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-10-21

### Added
- **Clipboard Watch mode** (`--watch`) — copy any video link and it downloads automatically.
- **Clip / GIF maker** (`--clip 0:32-0:41`, `--gif`) — download only a time range, optionally as a GIF.
- **Smart size preview** — estimated file size shown next to every quality before downloading.
- **Size-cap mode** (`--max-size 100M`) — automatically picks the best quality that fits.
- **SponsorBlock integration** (`--clean`, `--sb`) — sponsor/self-promo segments removed from the file.
- **Channel subscriptions** (`--subscribe`, `--sync`) — fetch only new uploads, duplicates impossible.
- **Search & download** (`--search`, or just type a phrase) — no URL needed.
- **Parallel downloads** (`--workers`) — multiple videos simultaneously.
- **Download archive** (`--archive`) — never download the same video twice.
- **Chapter splitting** (`--split-chapters`) — one file per chapter for albums/podcasts.
- **Batch mode** (`--file links.txt`) — one URL per line.
- pip-installable package with `ytd` console command (`pyproject.toml`).
- GitHub Actions: CI smoke tests + automatic Windows EXE build on release tags.

- **Full-screen terminal UI** (Textual): type `YT-DLP` anywhere to open it. Tabs for Download (with live progress), Clipboard Watch, Subscriptions, History and Settings. Use `--classic` for the old menu.
- Global `YT-DLP` command registered by `install.bat` (works in CMD, PowerShell and Windows Terminal).

- **Linux support:** `install.sh` (venv, `ytd` and `YT-DLP` commands, desktop entry, `--uninstall`), `build.sh` for a single-file Linux binary, and a Linux release asset.
- **Platform-aware hints:** ffmpeg, aria2 and clipboard install instructions now match the current OS.
- **Test suite:** `tests/test_core.py` (pytest), including a headless TUI start/quit test. Runs in CI on Windows and Linux.

### Fixed
- `--update` no longer tries to pip-upgrade itself inside the standalone EXE; it explains how to get the newest release.
- Settings and history are written atomically, so a crash can't corrupt them. A corrupt file is ignored instead of crashing.
- GIF conversion has a timeout and can no longer hang the app.
- Classic menu: the parallel-parts limit now matches the 1-16 clamp.
- Opening the downloads folder without `xdg-open` prints the path instead of failing silently.
- Clipboard Watch no longer misses links copied while a download is running (downloads are queued).
- Domain check now matches exact hosts, so unrelated sites such as `box.com` are no longer treated as supported.
- GIF conversion is only attempted on video files.
- SponsorBlock, chapter splitting, clips and GIFs now fail early with a clear message when ffmpeg is missing.
- `--max-size` is explained (instead of silently ignored) when used with MP3 mode.

### Performance
- Lower default resource use: 4 parallel fragments per file (was 8), 5 MB HTTP chunks (was 10 MB), progress redraws 4/s (configurable as `refresh_rate`), and the clipboard is read once per poll.
- Settings values are clamped to safe ranges.

### Changed
- Full English UI (menu, CLI, installer, docs).
- Settings gained: parallel-video count, SponsorBlock-clean by default.

## [1.0.0] - 2026-10-21

### Added
- Initial release: interactive terminal downloader for Windows.
- Quality selection (4K→144p), MP3 mode with cover art, playlists, subtitles,
  live progress (speed/ETA), download history, one-click installer, EXE builder.
