"""
Regenerate the README screenshots and the boot animation from the REAL app.

    python scripts/capture_screenshots.py

It runs the Textual UI headlessly, reads back the exact frame the app renders
(as ANSI colors), and draws it with a monospace font. Demo data is seeded into
a temporary folder, so your own downloads and settings are never touched.
"""
from __future__ import annotations

import asyncio
import io
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yt_downloader as core  # noqa: E402
import ytd_tui  # noqa: E402
from textual.widgets import Input, TabbedContent  # noqa: E402

OUT = ROOT / "assets" / "screenshots"
TUI_W, TUI_H = 112, 34
FONT_SIZE = 15
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/Library/Fonts/Menlo.ttc",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "C:/Windows/Fonts/consolab.ttf",
]
DEFAULT_FG = (230, 237, 243)
DEFAULT_BG = (0, 0, 0)
CHROME_BG = (42, 42, 42)


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("No monospace font found. Install DejaVu Sans Mono or Consolas.")


FONT = _font(FONT_CANDIDATES, FONT_SIZE)
FONT_B = _font(BOLD_CANDIDATES, FONT_SIZE)
CELL_W = round(FONT.getlength("M"))
CELL_H = round(FONT_SIZE * 1.42)

# ---------------------------------------------------------------- ANSI ----
_BASIC = [
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
]


def _c256(n: int) -> tuple[int, int, int]:
    if n < 16:
        return _BASIC[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    v = 8 + (n - 232) * 10
    return v, v, v


def parse_ansi(text: str) -> list[list[tuple]]:
    """Return rows of cells: (char, fg, bg, bold, dim)."""
    rows: list[list[tuple]] = [[]]
    fg = bg = None
    bold = dim = rev = False
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-ln-z]", "", text)  # drop cursor/erase codes, keep SGR (m)
    for token in re.split(r"(\x1b\[[0-9;]*m)", text):
        if token.startswith("\x1b["):
            codes = [int(c) for c in token[2:-1].split(";") if c.isdigit()] or [0]
            i = 0
            while i < len(codes):
                c = codes[i]
                if c == 0:
                    fg = bg = None; bold = dim = rev = False
                elif c == 1: bold = True
                elif c == 2: dim = True
                elif c == 7: rev = True
                elif c in (22,): bold = dim = False
                elif c == 27: rev = False
                elif c == 39: fg = None
                elif c == 49: bg = None
                elif 30 <= c <= 37: fg = _BASIC[c - 30]
                elif 90 <= c <= 97: fg = _BASIC[c - 90 + 8]
                elif 40 <= c <= 47: bg = _BASIC[c - 40]
                elif 100 <= c <= 107: bg = _BASIC[c - 100 + 8]
                elif c in (38, 48) and i + 1 < len(codes):
                    if codes[i + 1] == 2 and i + 4 < len(codes):
                        rgb = tuple(codes[i + 2:i + 5]); i += 4
                    elif codes[i + 1] == 5 and i + 2 < len(codes):
                        rgb = _c256(codes[i + 2]); i += 2
                    else:
                        i += 1; continue
                    if c == 38: fg = rgb
                    else: bg = rgb
                i += 1
            continue
        for ch in token:
            if ch == "\n":
                rows.append([])
                continue
            f, b = fg, bg
            if rev:
                f, b = (b or DEFAULT_BG), (f or DEFAULT_FG)
            rows[-1].append((ch, f, b, bold, dim))
    if not rows[-1]:
        rows.pop()
    return rows


def _glyph_bytes(ch: str) -> bytes:
    img = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(img).text((4, 4), ch, font=FONT, fill=255)
    return img.tobytes()


_NOTDEF = _glyph_bytes("\uffff")          # what the font draws for "no such glyph"
_coverage: dict[str, bool] = {}


def _covered(ch: str) -> bool:
    if ch not in _coverage:
        _coverage[ch] = _glyph_bytes(ch) != _NOTDEF
    return _coverage[ch]


def draw_frame(ansi: str, title: str, cols: int | None = None) -> Image.Image:
    rows = parse_ansi(ansi)
    cols = cols or max((len(r) for r in rows), default=1)
    pad = 18
    bar = 36
    width = cols * CELL_W + pad * 2
    height = bar + len(rows) * CELL_H + pad * 2
    img = Image.new("RGB", (width, height), DEFAULT_BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, bar], fill=CHROME_BG)
    for i, color in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        d.ellipse([14 + i * 20, 12, 26 + i * 20, 24], fill=color)
    tf = _font(BOLD_CANDIDATES, 14)
    tw = d.textlength(title, font=tf)
    d.text(((width - tw) / 2, 10), title, font=tf, fill=(200, 200, 200))
    for y, row in enumerate(rows):
        top = bar + pad + y * CELL_H
        for x, (ch, fg, bg, bold, dim) in enumerate(row):
            left = pad + x * CELL_W
            if bg:
                d.rectangle([left, top, left + CELL_W, top + CELL_H], fill=bg)
            if ch == " " or not ch.strip() or not _covered(ch):
                continue
            color = fg or DEFAULT_FG
            if dim:
                base = bg or DEFAULT_BG
                color = tuple(int(base[k] + (color[k] - base[k]) * 0.55) for k in range(3))
            d.text((left, top + 1), ch, font=FONT_B if bold else FONT, fill=color)
    return img


def capture_app(app: ytd_tui.YtdTui) -> str:
    console = Console(
        width=app.size.width, height=app.size.height, file=io.StringIO(),
        force_terminal=True, color_system="truecolor", record=True,
        legacy_windows=False, safe_box=False,
    )
    render = app.screen._compositor.render_update(
        full=True, screen_stack=app._background_screens, simplify=False
    )
    console.print(render)
    return console.export_text(styles=True, clear=True)


# -------------------------------------------------------- demo environment ----
DEMO_SUBS = [
    {"url": "https://www.youtube.com/@TechLabDemo", "name": "Tech Lab", "mode": "video",
     "quality": "best", "added": "2026-10-02"},
    {"url": "https://www.youtube.com/@SynthwaveDemo", "name": "Synthwave Radio", "mode": "audio",
     "quality": "best", "added": "2026-10-09"},
    {"url": "https://www.youtube.com/playlist?list=DemoLectures", "name": "Lecture Series",
     "mode": "video", "quality": "720", "added": "2026-10-15"},
]
DEMO_HISTORY = [
    {"title": "Me at the zoo", "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "mode": "video",
     "file": "~/Downloads/YouTube/Me at the zoo.mp4", "date": "2026-10-21 16:02"},
    {"title": "Synthwave Mix - 1 Hour Night Drive", "url": "", "mode": "audio",
     "file": "~/Downloads/YouTube/Synthwave Mix.mp3", "date": "2026-10-21 16:20"},
    {"title": "Lecture 04 - Graph Algorithms", "url": "", "mode": "video",
     "file": "~/Downloads/YouTube/Lecture 04.mp4", "date": "2026-10-21 17:45"},
]
DEMO_INFO = {
    "title": "Synthwave Mix - 1 Hour Night Drive", "duration": 3725, "view_count": 2140000,
    "like_count": 48200, "uploader": "Synthwave Radio",
    "formats": [
        {"vcodec": "avc1", "acodec": "none", "height": 2160, "filesize": 2_950_000_000},
        {"vcodec": "avc1", "acodec": "none", "height": 1080, "filesize": 1_480_000_000},
        {"vcodec": "avc1", "acodec": "none", "height": 720, "filesize": 610_000_000},
        {"vcodec": "avc1", "acodec": "none", "height": 480, "filesize": 310_000_000},
        {"vcodec": "avc1", "acodec": "none", "height": 360, "filesize": 160_000_000},
        {"vcodec": "none", "acodec": "mp4a", "filesize": 58_000_000},
    ],
}


def setup_demo_environment(tmp: Path) -> dict:
    """Point all storage at a temp folder, seed demo data, and fake a fully set-up PC."""
    core.CONFIG_DIR = tmp / ".ytdpro"
    core.CONFIG_FILE = core.CONFIG_DIR / "config.json"
    core.HISTORY_FILE = core.CONFIG_DIR / "history.json"
    core.SUBS_FILE = core.CONFIG_DIR / "subscriptions.json"
    core.ARCHIVE_FILE = core.CONFIG_DIR / "archive.txt"
    core.save_subscriptions(DEMO_SUBS)
    core._write_text_atomic(core.HISTORY_FILE, __import__("json").dumps(DEMO_HISTORY))
    core.ffmpeg_ok = lambda: True                # screenshots show a fully set-up PC
    core.get_clipboard = lambda: ""              # clipboard available (empty)
    config = dict(core.DEFAULT_CONFIG, download_dir="~/Downloads/YouTube", clean_default=True)
    return config


# ---------------------------------------------------------------- scenes ----
async def capture_tui(config: dict, out: dict) -> None:
    app = ytd_tui.YtdTui(config)
    async with app.run_test(size=(TUI_W, TUI_H)) as pilot:
        boot_frames = []
        for _ in range(40):
            await pilot.pause(0.22)
            if not isinstance(app.screen, ytd_tui.BootScreen):
                break
            boot_frames.append(capture_app(app))
        # wait for the boot screen to finish and hand over
        for _ in range(60):
            if not isinstance(app.screen, ytd_tui.BootScreen):
                break
            await pilot.pause(0.2)
        await pilot.pause(0.4)
        out["boot_frames"] = boot_frames
        out["boot_final"] = boot_frames[-1]

        # --- Download tab ----------------------------------------------------
        app.query_one("#url", Input).value = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        app._log("[dim]+ queued:[/dim] https://www.youtube.com/watch?v=jNQXAC9IVRw")
        app._log("[green]✓ Saved:[/green] Me at the zoo.mp4")
        app._log("[dim]+ queued:[/dim] https://www.youtube.com/watch?v=8ANNsMAg7T8")
        app._set_progress("Synthwave Mix - 1 Hour Night Drive.mp4", 412_000_000, 660_000_000)
        app._set_status = app._set_status  # keep
        app.query_one("#status").update("Synthwave Mix - 1 Hour Night Drive.mp4   62%   393.0 MB / 629.4 MB")
        await pilot.pause(0.6)
        out["download"] = capture_app(app)

        # --- Watch tab -------------------------------------------------------
        app.query_one(TabbedContent).active = "tab-watch"
        await pilot.pause(0.2)
        app.query_one("#watch-switch").value = True
        await pilot.pause(0.3)
        app._bridge_log("[bold green]>> Watch:[/bold green] https://youtu.be/8ANNsMAg7T8")
        app._bridge_log("[dim]+ queued:[/dim] https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        await pilot.pause(0.5)
        out["watch"] = capture_app(app)
        app.query_one("#watch-switch").value = False
        await pilot.pause(0.3)

        # --- Subscriptions ----------------------------------------------------
        app.query_one(TabbedContent).active = "tab-subs"
        app._refresh_subs()
        await pilot.pause(0.6)
        out["subs"] = capture_app(app)

        # --- History ----------------------------------------------------------
        app.query_one(TabbedContent).active = "tab-history"
        app._refresh_history()
        await pilot.pause(0.6)
        out["history"] = capture_app(app)

        # --- Settings ---------------------------------------------------------
        app.query_one(TabbedContent).active = "tab-settings"
        await pilot.pause(0.6)
        out["settings"] = capture_app(app)


def capture_cli(config: dict) -> str:
    """The classic CLI: banner plus the quality picker with live size estimates."""
    console = Console(width=100, file=io.StringIO(), force_terminal=True,
                      color_system="truecolor", record=True)
    core.console = console
    core.Prompt.ask = lambda *a, **k: "5"       # pick 720p
    core.show_banner(config)
    console.print("[bold]$ ytd https://www.youtube.com/watch?v=jNQXAC9IVRw[/bold]\n")
    core.choose_quality(DEMO_INFO)
    return console.export_text(styles=True, clear=True)


def save(img: Image.Image, name: str) -> Path:
    path = OUT / name
    img.save(path, optimize=True)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ytd-shots-"))
    config = setup_demo_environment(tmp)

    out: dict = {}
    asyncio.run(capture_tui(config, out))
    cli_text = capture_cli(config)

    shots = {
        "01-boot.png": (out["boot_final"], "YT-DLP"),
        "02-download.png": (out["download"], "YT-DLP"),
        "03-watch.png": (out["watch"], "YT-DLP"),
        "04-subscriptions.png": (out["subs"], "YT-DLP"),
        "05-history.png": (out["history"], "YT-DLP"),
        "06-settings.png": (out["settings"], "YT-DLP"),
    }
    for name, (ansi, title) in shots.items():
        img = draw_frame(ansi, title, cols=TUI_W)
        save(img, name)
        print("saved", name, img.size)

    cli_img = draw_frame(cli_text, "ytd — classic CLI", cols=100)
    save(cli_img, "07-cli-quality.png")
    print("saved 07-cli-quality.png", cli_img.size)

    # boot animation: every captured boot frame, with the last frame held
    frames = [draw_frame(f, "YT-DLP", cols=TUI_W) for f in out["boot_frames"]]
    frames += [frames[-1]] * 8
    frames[0].save(OUT / "boot.gif", save_all=True, append_images=frames[1:],
                   duration=220, loop=0, optimize=True)
    print("saved boot.gif", len(frames), "frames")
    print("done ->", OUT)


if __name__ == "__main__":
    main()
