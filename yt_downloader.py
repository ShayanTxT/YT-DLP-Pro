#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================================
  YT-DLP Pro — the YouTube downloader that works while you browse.
========================================================================================

Not just another yt-dlp wrapper. Feature highlights:

  * CLIPBOARD WATCH  - copy a link anywhere, it downloads automatically.   --watch
  * CLIP & GIF MAKER - download only 0:32-0:41 of a video, as MP4 or GIF.  --clip / --gif
  * SMART SIZES      - see the exact MB per quality BEFORE downloading,
                       or say "best quality under 100 MB".                  --max-size
  * SPONSORBLOCK     - sponsor segments & intros removed automatically.    --clean
  * SUBSCRIPTIONS    - follow channels; one command grabs only new uploads --sync
  * SEARCH & GRAB    - no URL needed:  ytd "lofi hip hop radio"
  * PARALLEL ENGINE  - several videos at once, multi-part fragment boost,  --workers
                       resume support, optional aria2c accelerator.

Quick start:
    yt_downloader.py                            Interactive menu
    yt_downloader.py <URL>                      Instant download (best quality)
    yt_downloader.py <URL> -q 720               Specific quality
    yt_downloader.py <URL> -a                   Audio only (MP3 320kbps + cover art)
    yt_downloader.py <PLAYLIST> -p              Whole playlist
    yt_downloader.py --watch                    Clipboard Watch mode
    yt_downloader.py <URL> --clip 0:32-0:41 --gif
    yt_downloader.py --help                     Everything else
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
try:
    import yt_dlp
    from yt_dlp.utils import DownloadError

    try:
        from yt_dlp.utils import download_range_func
    except ImportError:  # very old yt-dlp
        download_range_func = None
except ImportError:
    print("Missing dependency: yt-dlp\nInstall everything with:  pip install -r requirements.txt")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("Missing dependency: rich\nInstall everything with:  pip install -r requirements.txt")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants & config
# ---------------------------------------------------------------------------
APP_NAME = "YT-DLP Pro"
VERSION = "2.0.0"
CONFIG_DIR = Path.home() / ".ytdpro"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
SUBS_FILE = CONFIG_DIR / "subscriptions.json"
ARCHIVE_FILE = CONFIG_DIR / "archive.txt"

console = Console()

# UI bridge: when the full-screen TUI is running it installs these callbacks so that
# download messages and progress go to its widgets instead of the terminal.
_ui_log = None        # callable(markup: str) | None
_ui_progress = None   # callable(name: str, done: int, total: int | None) | None


def say(markup: str) -> None:
    """Print a status line (console normally, the TUI log when the TUI is active)."""
    if _ui_log is not None:
        _ui_log(markup)
    else:
        console.print(markup)
_history_lock = threading.Lock()

# Quality key -> yt-dlp format selector (best video <= height + best audio)
QUALITY_FORMATS = {
    "best": "bv*+ba/b",
    "2160": "bv*[height<=2160]+ba/b[height<=2160]/b",
    "1440": "bv*[height<=1440]+ba/b[height<=1440]/b",
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "480": "bv*[height<=480]+ba/b[height<=480]/b",
    "360": "bv*[height<=360]+ba/b[height<=360]/b",
    "240": "bv*[height<=240]+ba/b[height<=240]/b",
    "144": "bv*[height<=144]+ba/b[height<=144]/b",
}
QUALITY_ORDER = ["2160", "1440", "1080", "720", "480", "360", "240", "144"]
QUALITY_LABELS = {
    "best": "Best available",
    "2160": "4K Ultra HD  (2160p)",
    "1440": "2K QHD       (1440p)",
    "1080": "Full HD      (1080p)",
    "720": "HD           (720p)",
    "480": "SD           (480p)",
    "360": "360p  (small)",
    "240": "240p  (very small)",
    "144": "144p  (minimum size)",
}

# SponsorBlock categories removed by --clean
SB_DEFAULT = {"sponsor", "selfpromo", "interaction"}
SB_ALL = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "music_offtopic", "filler"]

# Domains that trigger Clipboard Watch auto-downloads
WATCH_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "vimeo.com",
    "x.com", "twitter.com", "twitch.tv", "soundcloud.com", "dailymotion.com",
    "facebook.com", "reddit.com",
)
URL_RE = re.compile(r"https?://[^\s<>\"'\)]+")

DEFAULT_CONFIG = {
    "download_dir": str(Path.home() / "Downloads" / "YouTube"),
    "quality": "best",
    "concurrent_fragments": 4,   # parallel fragments per file (speed vs. resources)
    "parallel_videos": 2,        # videos downloaded simultaneously
    "refresh_rate": 4,           # progress-bar redraws per second (lower = less CPU)
    "use_aria2": False,          # aria2c external accelerator
    "clean_default": False,      # SponsorBlock-clean every download
    "sync_scan_limit": 15,       # how many recent uploads a --sync scans per channel
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def human_size(num: float | None) -> str:
    if not num:
        return "—"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_views(views: int | None) -> str:
    if not views:
        return "—"
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


def ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_hint() -> str:
    """How to install ffmpeg on the current OS."""
    if sys.platform == "win32":
        return "winget install Gyan.FFmpeg"
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg   (Fedora: sudo dnf install ffmpeg | Arch: sudo pacman -S ffmpeg)"


def aria2_hint() -> str:
    if sys.platform == "win32":
        return "winget install aria2.aria2"
    if sys.platform == "darwin":
        return "brew install aria2"
    return "sudo apt install aria2"


def clipboard_hint() -> str:
    """How to enable Clipboard Watch on the current OS."""
    if sys.platform.startswith("linux"):
        return "pip install pyperclip  and  sudo apt install xclip   (Wayland: wl-clipboard)"
    return "pip install pyperclip"


def _write_text_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash can never leave a half-written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def looks_like_url(text: str) -> bool:
    return "://" in text or text.startswith(("www.", "youtube.com", "youtu.be"))


def parse_timestamp(text: str) -> float:
    """'90' / '1:30' / '1:02:05.5' -> seconds."""
    total = 0.0
    for part in text.strip().split(":"):
        total = total * 60 + float(part)
    return total


def parse_clip(text: str) -> tuple[float, float]:
    """'0:32-0:41' -> (32.0, 41.0). Raises ValueError on bad input."""
    m = re.match(r"^\s*([\d:.]+)\s*-\s*([\d:.]+)\s*$", text or "")
    if not m:
        raise ValueError("Use the format START-END, e.g. 0:32-0:41")
    start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
    if end <= start:
        raise ValueError("Clip end must be after the start.")
    return start, end


def parse_size(text: str) -> int:
    """'100' / '100M' / '1.5G' / '500K' -> bytes (bare number = MB)."""
    m = re.match(r"^\s*([\d.]+)\s*([KMG])?B?\s*$", (text or "").upper())
    if not m:
        raise ValueError("Use a size like 100M, 1.5G or 500K.")
    value = float(m.group(1))
    factor = {"K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2) or "M"]
    return int(value * factor)


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return config


def save_config(config: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(CONFIG_FILE, json.dumps(config, ensure_ascii=False, indent=2))
    except OSError:
        pass


def load_history() -> list[dict]:
    try:
        if HISTORY_FILE.exists():
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return []


def add_history(entry: dict) -> None:
    with _history_lock:
        history = load_history()
        history.append(entry)
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _write_text_atomic(HISTORY_FILE, json.dumps(history[-500:], ensure_ascii=False, indent=2))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Subscriptions storage
# ---------------------------------------------------------------------------
def load_subscriptions() -> list[dict]:
    try:
        if SUBS_FILE.exists():
            return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_subscriptions(subs: list[dict]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(SUBS_FILE, json.dumps(subs, ensure_ascii=False, indent=2))
    except OSError:
        pass


def add_subscription(url: str, name: str = "", mode: str = "video", quality: str = "best") -> bool:
    subs = load_subscriptions()
    if any(s["url"] == url for s in subs):
        return False
    subs.append(
        {
            "url": url,
            "name": name or url,
            "mode": mode,
            "quality": quality,
            "added": datetime.now().strftime("%Y-%m-%d"),
        }
    )
    save_subscriptions(subs)
    return True


def remove_subscription(match: str) -> bool:
    subs = load_subscriptions()
    kept = [s for s in subs if match not in (s["url"], s["name"])]
    if len(kept) == len(subs):
        return False
    save_subscriptions(kept)
    return True


# ---------------------------------------------------------------------------
# Clipboard access (pyperclip if present, native Win32 fallback)
# ---------------------------------------------------------------------------
def get_clipboard() -> str | None:
    """Return clipboard text, or None when no clipboard is available."""
    try:
        import pyperclip  # type: ignore

        return pyperclip.paste() or ""
    except Exception:  # noqa: BLE001
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            CF_UNICODETEXT = 13
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                try:
                    return ctypes.c_wchar_p(ptr).value or ""
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# yt-dlp logger + Rich progress bridge
# ---------------------------------------------------------------------------
def deno_install_hint() -> str:
    if os.name == "nt":
        return "winget install DenoLand.Deno"
    return "curl -fsSL https://deno.land/install.sh | sh"


class YtdLogger:
    """Silence yt-dlp chatter; surface only what matters."""

    _js_hint_shown = False  # yt-dlp repeats the JS-runtime warning for every job; show it once

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        low = msg.lower()
        if "javascript runtime" in low:
            if not YtdLogger._js_hint_shown:
                YtdLogger._js_hint_shown = True
                say("[yellow]  ! YouTube needs the Deno JavaScript runtime for all formats.[/yellow]")
                say(f"[yellow]    Install it once, then restart: {deno_install_hint()}[/yellow]")
            return
        if "ffmpeg" not in low:
            say(f"[dim yellow]  ! {msg}[/dim yellow]")

    def error(self, msg: str) -> None:
        say(f"[red]  x {msg}[/red]")


class ProgressTracker:
    """Feed yt-dlp download hooks into a Rich progress display."""

    PP_MESSAGES = {
        "Merger": "Merging audio + video...",
        "FFmpegExtractAudio": "Converting to MP3...",
        "FFmpegVideoConvertor": "Converting video format...",
        "FFmpegMetadata": "Writing metadata...",
        "EmbedThumbnail": "Embedding cover art...",
        "FFmpegEmbedSubtitle": "Embedding subtitles...",
        "FFmpegSubtitlesConvertor": "Converting subtitles...",
        "FFmpegSplitChapters": "Splitting into chapters...",
        "ModifyChapters": "Removing sponsor segments...",
        "SponsorBlock": "Fetching SponsorBlock data...",
    }

    def __init__(self, progress: Progress | None):
        self.progress = progress
        self.tasks: dict[str, int] = {}

    def hook(self, d: dict) -> None:
        status = d.get("status")
        filename = d.get("filename", "")
        name = Path(filename).name
        display = (name[:37] + "...") if len(name) > 40 else (name or "Downloading...")

        if _ui_progress is not None:
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                _ui_progress(display, d.get("downloaded_bytes", 0) or 0, total)
            elif status == "finished":
                total = d.get("total_bytes") or d.get("downloaded_bytes", 0) or 0
                _ui_progress(display, total, total or None)
            return

        if self.progress is None:
            return
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if filename not in self.tasks:
                self.tasks[filename] = self.progress.add_task(display, total=total)
            self.progress.update(self.tasks[filename], completed=downloaded, total=total)
        elif status == "finished" and filename in self.tasks:
            total = d.get("total_bytes") or d.get("downloaded_bytes", 0)
            self.progress.update(self.tasks[filename], completed=total, total=total or None)

    def pp_hook(self, d: dict) -> None:
        if d.get("status") == "started":
            msg = self.PP_MESSAGES.get(d.get("postprocessor", ""))
            if msg:
                say(f"[dim cyan]  ~ {msg}[/dim cyan]")


def make_progress(refresh: float = 4.0) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=28),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        refresh_per_second=refresh,
    )


def resolve_final_path(filepath: str) -> str:
    """yt-dlp sometimes reports the pre-conversion filename; find the real one."""
    if not filepath:
        return filepath
    p = Path(filepath)
    if p.exists():
        return str(p)
    for ext in (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus"):
        cand = p.with_suffix(ext)
        if cand.exists():
            return str(cand)
    return filepath


# ---------------------------------------------------------------------------
# Core downloader
# ---------------------------------------------------------------------------
class Downloader:
    def __init__(self, config: dict, cookies_browser: str | None = None):
        self.config = config
        self.cookies_browser = cookies_browser

    # -- Build yt-dlp options ------------------------------------------------
    def _build_opts(
        self,
        tracker: ProgressTracker,
        *,
        mode: str = "video",
        quality: str = "best",
        fmt: str | None = None,
        playlist: bool = False,
        subs: bool = False,
        embed_subs: bool = False,
        playlist_items: str | None = None,
        clip: tuple[float, float] | None = None,
        sb_remove: set[str] | None = None,
        split_chapters: bool = False,
        use_archive: bool = False,
        break_on_existing: bool = False,
        playlist_end: int | None = None,
    ) -> dict:
        download_dir = Path(self.config["download_dir"]).expanduser()
        download_dir.mkdir(parents=True, exist_ok=True)

        if playlist:
            default_tmpl = str(
                download_dir / "%(playlist_title)s" / "%(playlist_index)03d - %(title)s.%(ext)s"
            )
        elif clip:
            default_tmpl = str(download_dir / "%(title)s [clip %(section_start)s-%(section_end)s].%(ext)s")
        else:
            default_tmpl = str(download_dir / "%(title)s.%(ext)s")

        outtmpl: str | dict = default_tmpl
        if split_chapters:
            outtmpl = {
                "default": default_tmpl,
                "chapter": str(
                    download_dir / "%(title)s" / "%(section_number)02d - %(section_title)s.%(ext)s"
                ),
            }

        opts: dict = {
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": YtdLogger(),
            "progress_hooks": [tracker.hook],
            "postprocessor_hooks": [tracker.pp_hook],
            "continuedl": True,  # resume partial downloads
            "noplaylist": not playlist,
            "windowsfilenames": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "socket_timeout": 30,
            # Speed: parallel fragments + large HTTP chunks
            "concurrent_fragment_downloads": int(self.config.get("concurrent_fragments", 4)),
            "http_chunk_size": 5 * 1024 * 1024,  # bounded memory per connection
        }

        if self.cookies_browser:
            opts["cookiesfrombrowser"] = (self.cookies_browser,)

        if self.config.get("use_aria2") and shutil.which("aria2c"):
            opts["external_downloader"] = "aria2c"
            opts["external_downloader_args"] = {"aria2c": ["-x", "16", "-s", "16", "-k", "1M"]}

        if playlist_items:
            opts["playlist_items"] = playlist_items
        if playlist_end:
            opts["playlistend"] = playlist_end
        if use_archive:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            opts["download_archive"] = str(ARCHIVE_FILE)
        if break_on_existing:
            opts["break_on_existing"] = True

        # Clip download (only the time range is fetched for DASH streams)
        if clip and download_range_func is not None:
            opts["download_ranges"] = download_range_func(None, [clip])
            opts["force_keyframes_at_cuts"] = True  # frame-accurate cuts

        postprocessors: list[dict] = []

        if mode == "audio":
            opts["format"] = "ba/b"
            opts["writethumbnail"] = True
            postprocessors += [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ]
        else:
            opts["format"] = fmt or QUALITY_FORMATS.get(quality, "bv*+ba/b")
            if ffmpeg_ok():
                opts["merge_output_format"] = "mp4"

        if subs or embed_subs:
            opts.update(
                {
                    "writesubtitles": True,
                    "writeautomaticsub": True,
                    "subtitleslangs": ["en.*", "fa.*"],
                    "subtitlesformat": "srt/best",
                }
            )
            if embed_subs:
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        # SponsorBlock: fetch segments pre-process, cut them out post-process
        if sb_remove:
            postprocessors.append(
                {"key": "SponsorBlock", "categories": set(sb_remove), "when": "pre_process"}
            )
            postprocessors.append(
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": set(sb_remove),
                    "when": "post_process",
                }
            )
            postprocessors.append(
                {"key": "FFmpegMetadata", "add_chapters": True, "add_metadata": True, "when": "post_process"}
            )

        if split_chapters:
            postprocessors.append({"key": "FFmpegSplitChapters", "when": "post_process"})

        if postprocessors:
            opts["postprocessors"] = postprocessors

        return opts

    # -- Download one URL ----------------------------------------------------
    def _download_one(self, progress: Progress | None, url: str, mode: str, kw: dict) -> list[str]:
        tracker = ProgressTracker(progress)
        opts = self._build_opts(tracker, mode=mode, **kw)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            return self._record_history(info, mode)
        except DownloadError as e:
            say(f"\n  [bold red]x Failed:[/bold red] {url}\n    {e}")
        except Exception as e:  # noqa: BLE001
            say(f"\n  [bold red]x Unexpected error:[/bold red] {e}")
        return []

    # -- Download many (optionally in parallel) ------------------------------
    def download(
        self,
        urls: list[str],
        mode: str = "video",
        workers: int | None = None,
        **kw,
    ) -> list[str]:
        """Download all URLs. Returns the list of finished file paths."""
        progress = None if _ui_log is not None else make_progress(
            float(self.config.get("refresh_rate", 4) or 4)
        )
        workers = workers or int(self.config.get("parallel_videos", 1) or 1)
        files: list[str] = []

        with (progress if progress is not None else nullcontext()):
            if workers > 1 and len(urls) > 1 and not kw.get("playlist"):
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(self._download_one, progress, url, mode, kw): url
                        for url in urls
                    }
                    for fut in as_completed(futures):
                        try:
                            files += fut.result()
                        except Exception:  # noqa: BLE001 - already reported inside
                            pass
            else:
                for url in urls:
                    files += self._download_one(progress, url, mode, kw)
        return files

    # -- History -------------------------------------------------------------
    @staticmethod
    def _record_history(info: dict | None, mode: str) -> list[str]:
        if not info:
            return []
        entries = info.get("entries") or [info]
        files: list[str] = []
        for entry in entries:
            if not entry:
                continue
            filepath = ""
            downloads = entry.get("requested_downloads") or []
            if downloads:
                filepath = resolve_final_path(downloads[0].get("filepath", ""))
            add_history(
                {
                    "title": entry.get("title", "?"),
                    "url": entry.get("webpage_url", ""),
                    "mode": mode,
                    "file": filepath,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )
            if filepath:
                files.append(filepath)
        return files

    # -- Info without downloading --------------------------------------------
    @staticmethod
    def fetch_info(url: str) -> dict | None:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": YtdLogger()}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            say(f"  [red]x Could not fetch video info:[/red] {e}")
            return None

    @staticmethod
    def search_first(query: str) -> dict | None:
        """Return full info of the top YouTube search result for `query`."""
        opts = {"quiet": True, "no_warnings": True, "logger": YtdLogger()}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                result = ydl.extract_info(f"ytsearch1:{query}", download=False)
            entries = (result or {}).get("entries") or []
            return entries[0] if entries else None
        except Exception as e:  # noqa: BLE001
            say(f"  [red]x Search failed:[/red] {e}")
            return None

    # -- Smart size estimation ------------------------------------------------
    @staticmethod
    def estimate_quality_sizes(info: dict) -> dict[str, int]:
        """Estimated total bytes per quality tier (best video<=tier + best audio)."""
        duration = info.get("duration") or 0
        formats = info.get("formats") or []

        def fsize(f: dict) -> float:
            return (
                f.get("filesize")
                or f.get("filesize_approx")
                or ((f.get("tbr") or 0) * 1000 / 8 * duration if duration else 0)
            )

        def has_v(f: dict) -> bool:
            return f.get("vcodec") not in (None, "none")

        def has_a(f: dict) -> bool:
            return f.get("acodec") not in (None, "none")

        best_audio = max((fsize(f) for f in formats if has_a(f) and not has_v(f)), default=0)

        sizes: dict[str, int] = {}
        for key in ["best", *QUALITY_ORDER]:
            cap = 10**9 if key == "best" else int(key)
            v_only = [f for f in formats if has_v(f) and not has_a(f) and (f.get("height") or 0) <= cap]
            muxed = [f for f in formats if has_v(f) and has_a(f) and (f.get("height") or 0) <= cap]
            best_muxed = max((fsize(f) for f in muxed), default=0)
            if v_only:
                top = max(v_only, key=lambda f: ((f.get("height") or 0), fsize(f)))
                est = fsize(top) + best_audio
            else:
                est = best_muxed
            if est:
                sizes[key] = int(est)
        return sizes

    @staticmethod
    def pick_quality_for_size(info: dict, max_bytes: int) -> tuple[str, int]:
        """Highest quality whose estimated size fits under `max_bytes`."""
        sizes = Downloader.estimate_quality_sizes(info)
        for key in QUALITY_ORDER:  # highest -> lowest
            if sizes.get(key, 0) and sizes[key] <= max_bytes:
                return key, sizes[key]
        # Nothing fits: take the smallest available
        for key in reversed(QUALITY_ORDER):
            if sizes.get(key):
                return key, sizes[key]
        return "best", 0

    # -- Format listing --------------------------------------------------------
    def list_formats(self, url: str) -> None:
        with console.status("Fetching format list..."):
            info = self.fetch_info(url)
        if not info:
            return

        show_info_panel(info)
        table = Table(title="Available formats", show_lines=False)
        for col in ("ID", "Ext", "Resolution", "FPS", "Type", "Size (approx)"):
            table.add_column(col, style="bold")

        for f in info.get("formats", []):
            v, a = f.get("vcodec") not in (None, "none"), f.get("acodec") not in (None, "none")
            kind = "video+audio" if v and a else ("video only" if v else "audio only")
            res = f"{f.get('width')}x{f.get('height')}" if f.get("width") else (f.get("format_note") or "audio")
            table.add_row(
                str(f.get("format_id")),
                str(f.get("ext")),
                str(res),
                str(f.get("fps") or "—"),
                kind,
                human_size(f.get("filesize") or f.get("filesize_approx")),
            )
        console.print(table)
        console.print("Tip: download an exact combination with  [bold]-f 137+140[/bold]", style="dim")


# ---------------------------------------------------------------------------
# GIF conversion
# ---------------------------------------------------------------------------
def convert_to_gif(video_path: str, fps: int = 12, width: int = 480) -> Path | None:
    src = Path(video_path)
    if not src.exists() or not ffmpeg_ok():
        return None
    if src.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
        return None  # GIF only makes sense for video files
    out = src.with_suffix(".gif")
    vf = (
        f"fps={fps},scale={width}:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer"
    )
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-loop", "0", str(out)],
            capture_output=True,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if result.returncode == 0 and out.exists() else None


# ---------------------------------------------------------------------------
# Clipboard Watch mode
# ---------------------------------------------------------------------------
def _is_supported_host(url: str) -> bool:
    """True when the URL's host is one of WATCH_DOMAINS (exact or subdomain match)."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in WATCH_DOMAINS)


WATCH_POLL_SECONDS = 1.0


def watch_mode(downloader: Downloader, config: dict, mode: str, quality: str) -> None:
    first = get_clipboard()
    if first is None:
        console.print(
            "[red]x Clipboard access is unavailable.[/red] "
            f"Install the helper with:  [bold]{clipboard_hint()}[/bold]"
        )
        return

    sb = SB_DEFAULT if config.get("clean_default") else None
    console.print(
        Panel(
            "[bold]Clipboard Watch is ON[/bold]  -  copy any video link and it downloads itself.\n"
            f"Mode: {'MP3 audio' if mode == 'audio' else 'video'}   Quality: {QUALITY_LABELS.get(quality, quality)}\n"
            f"Saving to: {config['download_dir']}\n"
            "[dim]Links copied while a download runs are queued. Press Ctrl+C to stop.[/dim]",
            title="WATCH MODE",
            border_style="green",
        )
    )

    seen: set[str] = set(URL_RE.findall(first))   # links already on the clipboard are ignored
    last_text = first
    pending: "queue.Queue[str]" = queue.Queue()
    stats = {"done": 0}

    def worker() -> None:
        # One download at a time in the background, so copying a new link never blocks.
        while True:
            url = pending.get()
            console.print(f"\n[bold green]>> Downloading:[/bold green] {url}")
            files = downloader.download([url], mode=mode, quality=quality, sb_remove=sb, workers=1)
            if files:
                stats["done"] += 1
                console.print(f"[green]Saved:[/green] {Path(files[0]).name}")
            pending.task_done()

    threading.Thread(target=worker, daemon=True).start()

    try:
        while True:
            time.sleep(WATCH_POLL_SECONDS)
            text = get_clipboard()
            if not text or text == last_text:
                continue
            last_text = text
            for raw in URL_RE.findall(text):
                url = raw.rstrip(".,;:!?")
                if url in seen or not _is_supported_host(url):
                    continue
                seen.add(url)
                pending.put(url)
                console.print(f"[dim]+ queued:[/dim] {url}")
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Watch mode stopped.[/yellow] {stats['done']} file(s) saved this session.")


# ---------------------------------------------------------------------------
# Interactive UI
# ---------------------------------------------------------------------------
def show_banner(config: dict) -> None:
    title = Text()
    title.append("▶ YT-DLP ", style="bold red")
    title.append("Pro", style="bold white")
    title.append(f"  v{VERSION}", style="dim")

    body = Text()
    body.append_text(title)
    body.append("\nThe YouTube downloader that works while you browse.\n\n", style="white")
    body.append("Downloads folder: ", style="bold cyan")
    body.append(f"{config['download_dir']}\n")
    body.append("yt-dlp engine: ", style="bold cyan")
    body.append(f"{yt_dlp.version.__version__}    ")
    body.append("ffmpeg: ", style="bold cyan")
    body.append("OK" if ffmpeg_ok() else "NOT FOUND (needed for HD/MP3/GIF)",
                style="green" if ffmpeg_ok() else "yellow")

    console.print(Panel(body, border_style="red", padding=(1, 3)))
    if not ffmpeg_ok():
        console.print(f"Install ffmpeg:  [bold]{ffmpeg_hint()}[/bold]\n", style="dim yellow")


def show_info_panel(info: dict) -> None:
    likes = fmt_views(info.get("like_count"))
    console.print(
        Panel(
            f"[bold]{info.get('title', '?')}[/bold]\n"
            f"Duration: {fmt_duration(info.get('duration'))}   "
            f"Views: {fmt_views(info.get('view_count'))}   "
            f"Likes: {likes}   "
            f"Channel: {info.get('uploader', '—')}",
            border_style="cyan",
        )
    )


def ask_urls(prompt_text: str = "Paste the link (multiple links separated by spaces)") -> list[str]:
    raw = Prompt.ask(f"\n{prompt_text}").strip()
    return [u for u in raw.replace(",", " ").split() if u]


def choose_quality(info: dict | None) -> str:
    sizes: dict[str, int] = {}
    if info:
        show_info_panel(info)
        sizes = Downloader.estimate_quality_sizes(info)

    keys = ["best", *QUALITY_ORDER]
    table = Table(show_header=True, box=None, padding=(0, 2), title="Pick a quality")
    table.add_column("#", style="bold cyan")
    table.add_column("Quality")
    table.add_column("Est. size", style="green")
    for i, key in enumerate(keys, 1):
        table.add_row(str(i), QUALITY_LABELS[key], human_size(sizes.get(key)) if sizes else "—")
    console.print(table)
    choice = Prompt.ask("Quality", choices=[str(i) for i in range(1, len(keys) + 1)], default="1")
    return keys[int(choice) - 1]


def show_history() -> None:
    history = load_history()
    if not history:
        console.print("No downloads yet.", style="dim")
        return
    table = Table(title="Recent downloads", show_lines=False)
    table.add_column("Date", style="dim")
    table.add_column("Type")
    table.add_column("Title")
    for item in history[-15:][::-1]:
        table.add_row(
            item.get("date", ""),
            "audio" if item.get("mode") == "audio" else "video",
            (item.get("title") or "?")[:70],
        )
    console.print(table)


def open_download_folder(config: dict) -> None:
    folder = Path(config["download_dir"]).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=False)
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(folder)], check=False)
        else:
            console.print(f"Downloads folder: [bold]{folder}[/bold]")
    except OSError as e:
        console.print(f"[red]x Could not open folder:[/red] {e}")


def run_download_and_report(downloader: Downloader, config: dict, gif: bool = False, **kw) -> None:
    console.print()
    files = downloader.download(**kw)

    if gif and files:
        for f in files:
            with console.status("Creating GIF..."):
                gif_path = convert_to_gif(f)
            if gif_path:
                console.print(f"[green]GIF created:[/green] {gif_path.name}  ({human_size(gif_path.stat().st_size)})")
            else:
                console.print("[red]x GIF conversion failed (ffmpeg required).[/red]")

    if files:
        console.print(
            Panel(
                f"[bold green]Done - {len(files)} file(s) downloaded.[/bold green]\n"
                f"Saved to: {config['download_dir']}",
                border_style="green",
            )
        )
        if Confirm.ask("Open the downloads folder?", default=False):
            open_download_folder(config)


def subscriptions_menu(downloader: Downloader, config: dict) -> None:
    while True:
        subs = load_subscriptions()
        if subs:
            table = Table(title="Your subscriptions", show_lines=False)
            table.add_column("#", style="cyan")
            table.add_column("Name")
            table.add_column("Type")
            table.add_column("Since", style="dim")
            for i, s in enumerate(subs, 1):
                table.add_row(str(i), s["name"][:40], s["mode"], s.get("added", ""))
            console.print(table)
        else:
            console.print("No subscriptions yet.", style="dim")

        console.print(
            "  [cyan]1[/cyan] Add channel / playlist\n"
            "  [cyan]2[/cyan] Sync now (download only what's new)\n"
            "  [cyan]3[/cyan] Remove a subscription\n"
            "  [cyan]0[/cyan] Back"
        )
        choice = Prompt.ask("Select", choices=["0", "1", "2", "3"], default="0")

        if choice == "0":
            return
        elif choice == "1":
            url = Prompt.ask("Channel or playlist URL").strip()
            if not url:
                continue
            name = Prompt.ask("Display name (optional)", default="").strip()
            audio = Confirm.ask("Audio only (MP3) for this subscription?", default=False)
            if add_subscription(url, name, "audio" if audio else "video", config["quality"]):
                console.print("[green]Subscribed.[/green] Run Sync to fetch the latest videos.")
            else:
                console.print("[yellow]Already subscribed to that URL.[/yellow]")
        elif choice == "2":
            sync_subscriptions(downloader, config)
        elif choice == "3" and subs:
            idx = Prompt.ask("Remove which number?", choices=[str(i) for i in range(1, len(subs) + 1)])
            remove_subscription(subs[int(idx) - 1]["url"])
            console.print("[green]Removed.[/green]")


def sync_subscriptions(downloader: Downloader, config: dict) -> int:
    subs = load_subscriptions()
    if not subs:
        say("No subscriptions yet. Add one with --subscribe <channel URL>.", style="dim")
        return 0
    total = 0
    for sub in subs:
        say(f"\n[bold cyan]>> {sub['name']}[/bold cyan]  [dim]{sub['url']}[/dim]")
        files = downloader.download(
            [sub["url"]],
            mode=sub.get("mode", "video"),
            quality=sub.get("quality", "best"),
            playlist=True,
            use_archive=True,          # remembers what's already downloaded
            break_on_existing=True,   # stops at the first already-downloaded video
            playlist_end=int(config.get("sync_scan_limit", 15)),
            workers=1,
        )
        total += len(files)
        say(f"   {len(files)} new download(s).")
    say(f"\n[bold green]Sync complete: {total} new file(s).[/bold green]")
    return total


def settings_menu(config: dict) -> dict:
    while True:
        console.print(
            Panel(
                f"Downloads folder : [bold]{config['download_dir']}[/bold]\n"
                f"Default quality  : [bold]{QUALITY_LABELS.get(config['quality'], config['quality'])}[/bold]\n"
                f"Parallel parts   : [bold]{config.get('concurrent_fragments', 8)}[/bold] per file\n"
                f"Parallel videos  : [bold]{config.get('parallel_videos', 2)}[/bold]\n"
                f"aria2c boost     : [bold]{'ON' if config.get('use_aria2') else 'OFF'}[/bold]\n"
                f"SponsorBlock     : [bold]{'clean by default' if config.get('clean_default') else 'manual'}[/bold]",
                title="Settings",
                border_style="magenta",
            )
        )
        console.print(
            "  [cyan]1[/cyan] Change downloads folder\n"
            "  [cyan]2[/cyan] Default quality\n"
            "  [cyan]3[/cyan] Parallel parts per file (speed)\n"
            "  [cyan]4[/cyan] Parallel videos at once\n"
            "  [cyan]5[/cyan] Toggle aria2c accelerator\n"
            "  [cyan]6[/cyan] Toggle SponsorBlock-clean by default\n"
            "  [cyan]0[/cyan] Back"
        )
        choice = Prompt.ask("Select", choices=[str(i) for i in range(7)], default="0")

        if choice == "0":
            break
        elif choice == "1":
            new_dir = Prompt.ask("New folder path", default=str(config["download_dir"])).strip().strip('"')
            if new_dir:
                config["download_dir"] = new_dir
                Path(new_dir).expanduser().mkdir(parents=True, exist_ok=True)
                console.print("[green]Saved.[/green]")
        elif choice == "2":
            keys = list(QUALITY_LABELS)
            for i, key in enumerate(keys, 1):
                console.print(f"  [cyan]{i}[/cyan] {QUALITY_LABELS[key]}")
            idx = Prompt.ask("Default quality", choices=[str(i) for i in range(1, len(keys) + 1)], default="1")
            config["quality"] = keys[int(idx) - 1]
            console.print("[green]Saved.[/green]")
        elif choice == "3":
            val = Prompt.ask("Parallel parts per file (1-16, recommended 4)", default="4").strip()
            if val.isdigit() and 1 <= int(val) <= 16:
                config["concurrent_fragments"] = max(1, min(16, int(val)))
                console.print("[green]Saved.[/green]")
            else:
                console.print("[red]Enter a number between 1 and 32.[/red]")
        elif choice == "4":
            val = Prompt.ask("Videos at once (1-5)", default="2").strip()
            if val.isdigit() and 1 <= int(val) <= 5:
                config["parallel_videos"] = max(1, min(5, int(val)))
                console.print("[green]Saved.[/green]")
            else:
                console.print("[red]Enter a number between 1 and 5.[/red]")
        elif choice == "5":
            config["use_aria2"] = not config.get("use_aria2", False)
            if config["use_aria2"] and not shutil.which("aria2c"):
                console.print(f"[yellow]aria2c is not installed. Install:[/yellow] [bold]{aria2_hint()}[/bold]")
            console.print(f"[green]aria2c {'enabled' if config['use_aria2'] else 'disabled'}.[/green]")
        elif choice == "6":
            config["clean_default"] = not config.get("clean_default", False)
            console.print(
                f"[green]SponsorBlock-clean by default: {'ON' if config['clean_default'] else 'OFF'}[/green]"
            )
        save_config(config)
    return config


def interactive_main(config: dict) -> None:
    show_banner(config)
    downloader = Downloader(config)

    menu = (
        "\n[bold]What do you want to do?[/bold]\n"
        "  [cyan] 1[/cyan]  Quick download (best quality)\n"
        "  [cyan] 2[/cyan]  Download with quality + size preview\n"
        "  [cyan] 3[/cyan]  Audio only (MP3)\n"
        "  [cyan] 4[/cyan]  Playlist download\n"
        "  [cyan] 5[/cyan]  Clip / GIF maker        [dim](grab just a piece of a video)[/dim]\n"
        "  [cyan] 6[/cyan]  Search & download       [dim](no link needed)[/dim]\n"
        "  [cyan] 7[/cyan]  Clipboard Watch mode    [dim](copy a link -> auto-download)[/dim]\n"
        "  [cyan] 8[/cyan]  Subscriptions           [dim](auto-grab new uploads)[/dim]\n"
        "  [cyan] 9[/cyan]  Download history\n"
        "  [cyan]10[/cyan]  Open downloads folder\n"
        "  [cyan]11[/cyan]  Settings\n"
        "  [cyan] 0[/cyan]  Exit"
    )

    while True:
        try:
            console.print(menu)
            choice = Prompt.ask("Select", choices=[str(i) for i in range(12)], default="1")
            sb = SB_DEFAULT if config.get("clean_default") else None

            if choice == "0":
                console.print("[bold green]Bye! Happy downloading.[/bold green]")
                break

            elif choice == "1":
                urls = ask_urls()
                if urls:
                    run_download_and_report(downloader, config, urls=urls,
                                            quality=config["quality"], sb_remove=sb)

            elif choice == "2":
                urls = ask_urls()
                if urls:
                    with console.status("Fetching video info..."):
                        info = downloader.fetch_info(urls[0])
                    quality = choose_quality(info)
                    subs = Confirm.ask("Also download subtitles (EN/FA)?", default=False)
                    clean = sb or (SB_DEFAULT if Confirm.ask("Remove sponsor segments (SponsorBlock)?", default=False) else None)
                    run_download_and_report(downloader, config, urls=urls,
                                            quality=quality, subs=subs, sb_remove=clean)

            elif choice == "3":
                urls = ask_urls()
                if urls:
                    split = Confirm.ask("Split into separate files per chapter? (great for albums)", default=False)
                    run_download_and_report(downloader, config, urls=urls,
                                            mode="audio", split_chapters=split)

            elif choice == "4":
                urls = ask_urls("Paste the playlist link")
                if urls:
                    rng = Prompt.ask("Item range (e.g. 1-10) - empty = all", default="").strip()
                    items = rng.replace("-", ":") if rng else None
                    audio = Confirm.ask("Audio only (MP3)?", default=False)
                    archive = Confirm.ask("Skip videos already downloaded before?", default=True)
                    run_download_and_report(
                        downloader, config, urls=urls,
                        mode="audio" if audio else "video",
                        quality=config["quality"], playlist=True,
                        playlist_items=items, use_archive=archive, sb_remove=sb,
                    )

            elif choice == "5":
                urls = ask_urls()
                if urls:
                    clip_text = Prompt.ask("Clip range START-END (e.g. 0:32-0:41)").strip()
                    try:
                        clip = parse_clip(clip_text)
                    except ValueError as e:
                        console.print(f"[red]x {e}[/red]")
                        continue
                    as_gif = Confirm.ask("Also save as GIF?", default=False)
                    as_audio = False if as_gif else Confirm.ask("Audio only (MP3 clip)?", default=False)
                    run_download_and_report(
                        downloader, config, gif=as_gif, urls=urls,
                        mode="audio" if as_audio else "video",
                        quality="720" if as_gif else config["quality"],
                        clip=clip, workers=1,
                    )

            elif choice == "6":
                query = Prompt.ask("\nSearch YouTube for").strip()
                if query:
                    with console.status("Searching..."):
                        info = downloader.search_first(query)
                    if info:
                        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}"
                        quality = choose_quality(info)
                        if Confirm.ask("Download this one?", default=True):
                            run_download_and_report(downloader, config, urls=[url],
                                                    quality=quality, sb_remove=sb)

            elif choice == "7":
                audio = Confirm.ask("Watch in MP3 audio mode?", default=False)
                watch_mode(downloader, config, "audio" if audio else "video", config["quality"])

            elif choice == "8":
                subscriptions_menu(downloader, config)

            elif choice == "9":
                show_history()

            elif choice == "10":
                open_download_folder(config)

            elif choice == "11":
                config = settings_menu(config)
                downloader = Downloader(config)

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            try:
                if Confirm.ask("Exit the program?", default=True):
                    break
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ytd",
        description="YT-DLP Pro - the YouTube downloader that works while you browse.",
        epilog=(
            "Examples:\n"
            "  ytd https://youtu.be/xxxx                     Instant download (best quality)\n"
            "  ytd https://youtu.be/xxxx -q 720              720p\n"
            "  ytd https://youtu.be/xxxx -a                  MP3 only\n"
            "  ytd \"lofi hip hop radio\"                      Search & download (no URL!)\n"
            "  ytd --watch                                   Copy links -> auto-download\n"
            "  ytd <URL> --clip 0:32-0:41 --gif              Make a GIF of that moment\n"
            "  ytd <URL> --max-size 100M                     Best quality under 100 MB\n"
            "  ytd <URL> --clean                             Strip sponsor segments\n"
            "  ytd <PLAYLIST> -p --archive                   Playlist, skip already-downloaded\n"
            "  ytd --subscribe <CHANNEL_URL>                 Follow a channel\n"
            "  ytd --sync                                    Fetch new uploads from all channels\n"
            "  ytd                                           Interactive menu\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls", nargs="*", metavar="LINK_OR_SEARCH",
                   help="Video/playlist URL(s) - or a search phrase in quotes")
    p.add_argument("-a", "--audio", action="store_true", help="Audio only, MP3 320kbps with cover art")
    p.add_argument("-q", "--quality", choices=list(QUALITY_FORMATS), metavar="Q",
                   help="Video quality: best, 2160, 1440, 1080, 720, 480, 360, 240, 144")
    p.add_argument("-f", "--format", dest="fmt", metavar="FMT", help="Raw yt-dlp format selector (e.g. 137+140)")
    p.add_argument("--list-formats", action="store_true", help="List all available formats of a video")
    p.add_argument("-p", "--playlist", action="store_true", help="Download the whole playlist")
    p.add_argument("--items", dest="playlist_items", metavar="1:10", help="Playlist item range (e.g. 1:10)")
    p.add_argument("-o", "--output", metavar="DIR", help="Output folder")
    p.add_argument("-s", "--subs", action="store_true", help="Download EN/FA subtitles next to the video")
    p.add_argument("--embed-subs", action="store_true", help="Embed subtitles into the video file")
    p.add_argument("--browser-cookies", metavar="BROWSER",
                   help="Use browser cookies for restricted videos (chrome, edge, firefox)")
    p.add_argument("--file", dest="batch_file", metavar="FILE",
                   help="Text file with one URL per line (batch download)")
    p.add_argument("--search", metavar="QUERY", help="Search YouTube and download the top result")
    p.add_argument("--watch", action="store_true",
                   help="Clipboard Watch: auto-download every video link you copy")
    p.add_argument("--clip", metavar="START-END",
                   help="Download only a time range, e.g. --clip 0:32-0:41")
    p.add_argument("--gif", action="store_true", help="Also convert the result/clip into a GIF")
    p.add_argument("-C", "--clean", action="store_true",
                   help="Remove sponsor/self-promo segments via SponsorBlock")
    p.add_argument("--sb", metavar="CATS",
                   help=f"Custom SponsorBlock categories, comma-separated ({','.join(SB_ALL)})")
    p.add_argument("--split-chapters", action="store_true",
                   help="Split output into one file per chapter (albums, podcasts)")
    p.add_argument("--max-size", metavar="SIZE",
                   help="Automatically pick the best quality under this size (e.g. 100M, 1.5G)")
    p.add_argument("--archive", action="store_true",
                   help="Remember downloads; skip anything already downloaded before")
    p.add_argument("--workers", type=int, metavar="N", help="Videos to download simultaneously (default from settings)")
    p.add_argument("--subscribe", metavar="URL", help="Subscribe to a channel/playlist for --sync")
    p.add_argument("--name", metavar="NAME", help="Display name used with --subscribe")
    p.add_argument("--unsubscribe", metavar="URL_OR_NAME", help="Remove a subscription")
    p.add_argument("--subscriptions", action="store_true", help="List your subscriptions")
    p.add_argument("--sync", action="store_true", help="Download new uploads from all subscriptions")
    p.add_argument("--history", action="store_true", help="Show download history")
    p.add_argument("--update", action="store_true", help="Update the yt-dlp engine (fixes most errors)")
    p.add_argument("--classic", action="store_true", help="Use the classic text menu instead of the full-screen UI")
    p.add_argument("-v", "--version", action="version",
                   version=f"{APP_NAME} {VERSION}  |  yt-dlp {yt_dlp.version.__version__}")
    return p


def read_batch_file(path: str) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        console.print(f"[red]x Could not read batch file:[/red] {e}")
        return []
    urls = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def main(argv: list[str] | None = None) -> int:
    # Colors & UTF-8 on the Windows console
    if sys.platform == "win32":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    args = build_parser().parse_args(argv)
    config = load_config()

    # -- Maintenance commands -------------------------------------------------
    if args.update:
        if getattr(sys, "frozen", False):
            console.print("This is the standalone build. Download the newest release from GitHub to update.")
            return 0
        console.print("[bold cyan]Updating yt-dlp engine...[/bold cyan]")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], check=False
        )
        if result.returncode == 0:
            console.print("[green]Update finished.[/green]")
        else:
            console.print(
                "[red]Update failed.[/red] On Debian/Ubuntu/Fedora system Python is often locked. "
                "Use the project venv:  .venv/bin/python -m pip install -U yt-dlp   (Windows: .venv\\Scripts\\python -m pip install -U yt-dlp)"
            )
        return result.returncode

    if args.history:
        show_history()
        return 0

    # -- Subscription commands --------------------------------------------------
    if args.subscribe:
        ok = add_subscription(
            args.subscribe,
            args.name or "",
            "audio" if args.audio else "video",
            args.quality or config["quality"],
        )
        console.print("[green]Subscribed.[/green] Run [bold]ytd --sync[/bold] anytime to fetch new uploads."
                      if ok else "[yellow]Already subscribed to that URL.[/yellow]")
        return 0

    if args.unsubscribe:
        ok = remove_subscription(args.unsubscribe)
        console.print("[green]Unsubscribed.[/green]" if ok else "[red]No subscription matched.[/red]")
        return 0 if ok else 1

    if args.subscriptions:
        subs = load_subscriptions()
        if not subs:
            console.print("No subscriptions yet. Add one:  [bold]ytd --subscribe <channel URL>[/bold]", style="dim")
        else:
            table = Table(title="Subscriptions")
            table.add_column("Name")
            table.add_column("Type")
            table.add_column("URL", style="dim")
            for s in subs:
                table.add_row(s["name"], s["mode"], s["url"])
            console.print(table)
        return 0

    if args.output:
        config["download_dir"] = args.output

    downloader = Downloader(config, cookies_browser=args.browser_cookies)
    mode = "audio" if args.audio else "video"
    quality = args.quality or config["quality"]

    if args.sync:
        sync_subscriptions(downloader, config)
        return 0

    # -- Clipboard Watch ---------------------------------------------------------
    if args.watch:
        watch_mode(downloader, config, mode, quality)
        return 0

    # -- Assemble target URLs -----------------------------------------------------
    urls: list[str] = list(args.urls)
    if args.batch_file:
        urls += read_batch_file(args.batch_file)

    # Positional args that are not URLs = a search phrase:  ytd "lofi hip hop"
    search_query = args.search
    if urls and not any(looks_like_url(u) for u in urls) and not args.batch_file:
        search_query = " ".join(urls)
        urls = []

    if search_query:
        with console.status(f"Searching YouTube for '{search_query}'..."):
            info = downloader.search_first(search_query)
        if not info:
            console.print("[red]x No results found.[/red]")
            return 1
        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}"
        console.print(f"[bold]Top result:[/bold] {info.get('title', '?')}  [dim]({info.get('uploader', '')})[/dim]")
        urls = [url]

    # -- Download mode --------------------------------------------------------------
    if urls:
        if args.list_formats:
            downloader.list_formats(urls[0])
            return 0

        clip = None
        if args.clip:
            try:
                clip = parse_clip(args.clip)
            except ValueError as e:
                console.print(f"[red]x Invalid --clip value:[/red] {e}")
                return 1
            if download_range_func is None:
                console.print("[red]x Your yt-dlp is too old for --clip. Run: ytd --update[/red]")
                return 1

        sb_remove: set[str] | None = None
        if args.sb:
            sb_remove = {c.strip() for c in args.sb.split(",") if c.strip() in SB_ALL} or None
        elif args.clean or config.get("clean_default"):
            sb_remove = set(SB_DEFAULT)

        max_bytes = None
        if args.max_size:
            try:
                max_bytes = parse_size(args.max_size)
            except ValueError as e:
                console.print(f"[red]x Invalid --max-size value:[/red] {e}")
                return 1

        console.print(
            f"[bold red]YT-DLP Pro[/bold red]  -  {len(urls)} link(s)  -  saving to {config['download_dir']}"
        )
        if not ffmpeg_ok():
            console.print(
                "[yellow]! ffmpeg not found - HD merging/MP3/GIF may fail. "
                f"Install:[/yellow] [bold]{ffmpeg_hint()}[/bold]"
            )

        if not ffmpeg_ok() and (sb_remove or args.split_chapters or args.clip or args.gif):
            console.print(f"[red]x This option needs ffmpeg. Install: {ffmpeg_hint()}[/red]")
            return 1

        if max_bytes and mode == "audio":
            console.print("[yellow]! --max-size applies to video only; ignored for MP3.[/yellow]")
            max_bytes = None

        common = dict(
            fmt=args.fmt,
            playlist=args.playlist,
            subs=args.subs,
            embed_subs=args.embed_subs,
            playlist_items=args.playlist_items,
            clip=clip,
            sb_remove=sb_remove,
            split_chapters=args.split_chapters,
            use_archive=args.archive,
            workers=args.workers,
        )

        files: list[str] = []
        if max_bytes and mode == "video":
            # Smart mode: per-video quality so each file stays under the cap
            for url in urls:
                with console.status("Analyzing formats..."):
                    info = downloader.fetch_info(url)
                q, est = (downloader.pick_quality_for_size(info, max_bytes) if info else (quality, 0))
                console.print(
                    f"  Smart pick: [bold]{QUALITY_LABELS.get(q, q).strip()}[/bold] "
                    f"(~{human_size(est)} of max {human_size(max_bytes)})"
                )
                files += downloader.download([url], mode=mode, quality=q, **common)
        else:
            files = downloader.download(urls, mode=mode, quality=quality, **common)

        if args.gif and files:
            for f in files:
                with console.status("Creating GIF..."):
                    gif_path = convert_to_gif(f)
                if gif_path:
                    console.print(f"[green]GIF created:[/green] {gif_path}  ({human_size(gif_path.stat().st_size)})")
                else:
                    console.print("[red]x GIF conversion failed (ffmpeg required).[/red]")

        if files:
            console.print(
                f"\n[bold green]Done! {len(files)} file(s) downloaded.[/bold green]  {config['download_dir']}"
            )
            return 0
        console.print("\n[bold red]Nothing was downloaded.[/bold red]")
        return 1

    # -- Interactive mode -----------------------------------------------------------
    if args.classic or not (sys.stdin.isatty() and sys.stdout.isatty()):
        interactive_main(config)
        return 0
    try:
        from ytd_tui import run_tui
    except ImportError:
        console.print("[yellow]! Full-screen UI needs 'textual' (pip install -r requirements.txt). Using classic menu.[/yellow]")
        interactive_main(config)
        return 0
    run_tui(config)
    return 0


def cli() -> None:
    """Console-script entry point (pip install -> `ytd` command)."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Bye![/dim]")
        sys.exit(0)


if __name__ == "__main__":
    cli()
