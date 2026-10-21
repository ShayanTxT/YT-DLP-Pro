"""
YT-DLP Pro — full-screen terminal UI (Textual).

Launched automatically when you run `ytd` with no arguments in an interactive
terminal. Use `ytd --classic` for the original text menu.

Tabs:  Download · Watch · Subscriptions · History · Settings
Keys:  Ctrl+D focus link box · Ctrl+W toggle Clipboard Watch · Q quit
"""
from __future__ import annotations

import queue
import secrets
import threading
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

import yt_downloader as core


# ---------------------------------------------------------------------------
# Jobs run one at a time in a single background thread (keeps CPU/disk calm)
# ---------------------------------------------------------------------------
@dataclass
class Job:
    kind: str = "download"          # "download" | "sync"
    target: str = ""                # URL or search phrase
    mode: str = "video"
    quality: str = "best"
    clip: tuple | None = None
    sb: set | None = None
    subs: bool = False
    max_bytes: int | None = None


QUALITY_OPTIONS = [(core.QUALITY_LABELS[k].strip(), k) for k in ["best", *core.QUALITY_ORDER]]
MODE_OPTIONS = [("Video (MP4)", "video"), ("Audio (MP3, 320 kbps)", "audio")]


def _safe_text(markup: str) -> Text:
    try:
        return Text.from_markup(markup)
    except Exception:  # noqa: BLE001 - malformed markup from a message: show it raw
        return Text(markup)

# ---------------------------------------------------------------------------
# Boot splash (retro phosphor look)
# ---------------------------------------------------------------------------
_GLYPHS = {
    "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "-": ["        ", "        ", "█████╗  ", "╚════╝  ", "        ", "        "],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
}
LOGO_LINES = ["".join(_GLYPHS[c][i] for c in "YT-DLP") for i in range(6)]
GREEN = "#33ff66"
DIM_GREEN = "#1f9d4a"
DARK_GREEN = "#0f4d24"
RED = "#ff3b3b"


def boot_checks() -> list[tuple[str, str, str]]:
    """Real local checks shown on the boot screen: (status, name, detail)."""
    ver = getattr(core.yt_dlp.version, "__version__", "?")
    clip_ok = core.get_clipboard() is not None
    subs = len(core.load_subscriptions())
    hist = len(core.load_history())
    ff_ok = core.ffmpeg_ok()
    return [
        ("ok", "decoder", f"yt-dlp {ver} · mp4 · mkv · webm · mp3"),
        ("ok" if ff_ok else "!!", "ffmpeg",
         "merge · mp3 · clips · gif" if ff_ok else f"not found · run: {core.ffmpeg_hint()}"),
        ("ok" if clip_ok else "!!", "clipboard",
         "watch ready" if clip_ok else f"unavailable · {core.clipboard_hint()}"),
        ("ok", "subscriptions", f"{subs} channel(s) · sync ready"),
        ("ok", "history", f"{hist} item(s) on record"),
        ("ok", "sponsorblock", "segment cutter armed"),
        ("..", "handshake", "done"),
    ]


class BootScreen(Screen):
    """Shows the logo and the system checks line by line, then hands over to the app."""

    BINDINGS = [("enter", "skip", "Skip"), ("space", "skip", "Skip")]
    DEFAULT_CSS = """
    BootScreen { background: #000000; align: left top; padding: 2 4; }
    #logo { height: auto; margin-bottom: 1; }
    #tagline { height: auto; margin-bottom: 1; }
    #checks { height: auto; }
    #hint { height: 1; margin-top: 2; }
    """

    def __init__(self, checks: list[tuple[str, str, str]], session: str, version: str):
        super().__init__()
        self._checks = checks
        self._session = session
        self._version = version
        self._index = 0
        self._done = False
        self._checks_text = Text()

    def compose(self) -> ComposeResult:
        yield Static(Text("\n".join(LOGO_LINES), style=GREEN), id="logo")
        yield Static(
            Text(f"v{self._version} · downloader unit · session 0x{self._session}", style=DIM_GREEN),
            id="tagline",
        )
        yield Static("", id="checks")
        yield Static(Text("press ENTER to skip", style=DARK_GREEN), id="hint")

    def on_mount(self) -> None:
        self.set_interval(0.22, self._tick)

    def _tick(self) -> None:
        if self._done:
            return
        if self._index < len(self._checks):
            status, name, detail = self._checks[self._index]
            self._index += 1
            style = {"ok": GREEN, "!!": RED}.get(status, DIM_GREEN)
            line = Text()
            line.append(f"[{status}]", style=f"bold {style}")
            line.append(f" {name:<14}", style=f"bold {GREEN}")
            line.append(f" {detail}", style=(RED if status == "!!" else DIM_GREEN))
            line.append("\n")
            self._checks_text.append_text(line)
            self.query_one("#checks", Static).update(self._checks_text)
            return
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self.set_timer(0.8, self.action_skip)

    def action_skip(self) -> None:
        if self.is_attached and self.app.screen is self:
            self.dismiss()


class YtdTui(App):
    TITLE = "YT-DLP"
    SUB_TITLE = "downloader unit"

    CSS = """
    Screen { background: #000000; color: #33ff66; }
    Header { background: #000000; color: #33ff66; text-style: bold; }
    Footer { background: #0a2a14; color: #33ff66; }
    FooterKey { color: #33ff66; background: #0a2a14; }
    FooterKey .footer-key--key { color: #000000; background: #33ff66; text-style: bold; }
    Tabs { background: #000000; }
    Tab { color: #1f9d4a; }
    Tab.-active { color: #33ff66; text-style: bold; }
    Tab:hover { color: #33ff66; }
    Underline > .underline--bar { color: #33ff66; background: #0a2a14; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 1 2; background: #000000; }
    .row { height: auto; margin: 0 0 1 0; }
    .row > * { margin-right: 2; }
    .grow { width: 1fr; }
    Input { background: #000000; color: #33ff66; border: round #1f9d4a; }
    Input:focus { border: round #33ff66; }
    Input > .input--placeholder { color: #0f4d24; }
    Select { width: 26; }
    SelectCurrent { background: #000000; color: #33ff66; border: round #1f9d4a; }
    Label { color: #33ff66; }
    Switch { background: #000000; }
    Switch > .switch--slider { color: #0f4d24; background: #0a2a14; }
    Switch.-on > .switch--slider { color: #33ff66; }
    Button { background: #0a2a14; color: #33ff66; border: none; }
    Button:hover { background: #33ff66; color: #000000; }
    #go { background: #33ff66; color: #000000; text-style: bold; }
    #go:hover { background: #ffffff; color: #000000; }
    #bar { margin: 1 0 0 0; }
    #bar > .bar--bar { color: #33ff66; background: #0a2a14; }
    #bar > .bar--complete { color: #33ff66; }
    #status { color: #1f9d4a; margin: 0 0 1 0; }
    #log, #watch-log { height: 1fr; border: round #0f4d24; background: #000000; color: #33ff66; }
    #history-table, #subs-table { height: 1fr; border: round #0f4d24; background: #000000; color: #33ff66; }
    DataTable { background: #000000; color: #33ff66; }
    DataTable > .datatable--header { background: #0a2a14; color: #33ff66; text-style: bold; }
    DataTable > .datatable--cursor { background: #1f9d4a; color: #000000; }
    DataTable > .datatable--even-row { background: #000000; }
    DataTable > .datatable--odd-row { background: #050f08; }
    Label.dim, .dim { color: #1f9d4a; }
    .title { color: #33ff66; text-style: bold; margin-bottom: 1; }
    """

    BINDINGS = [
        ("ctrl+d", "focus_url", "Focus link"),
        ("ctrl+w", "toggle_watch", "Clipboard Watch"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.downloader = core.Downloader(config)
        self.jobs: "queue.Queue[Job | None]" = queue.Queue()
        self._watch_stop: threading.Event | None = None
        self._watch_thread: threading.Thread | None = None
        self._queued_urls: set[str] = set()   # every link queued this session, by hand or by Watch

    def _default_quality(self) -> str:
        keys = {k for _, k in QUALITY_OPTIONS}
        q = self.config.get("quality", "best")
        return q if q in keys else "best"

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-download"):
            with TabPane("⬇  Download", id="tab-download"):
                yield Input(
                    placeholder="Paste a link, or type a search phrase, then press Enter",
                    id="url",
                )
                with Horizontal(classes="row"):
                    yield Select(QUALITY_OPTIONS, value=self._default_quality(),
                                 allow_blank=False, id="quality")
                    yield Select(MODE_OPTIONS, value="video", allow_blank=False, id="mode")
                    yield Input(placeholder="Clip 0:32-0:41", id="clip", classes="grow")
                    yield Input(placeholder="Max size 100M", id="maxsize", classes="grow")
                with Horizontal(classes="row"):
                    yield Switch(value=bool(self.config.get("clean_default")), id="sb")
                    yield Label("SponsorBlock clean")
                    yield Switch(id="subs")
                    yield Label("Subtitles")
                    yield Button("Download", variant="success", id="go")
                yield ProgressBar(total=100, show_eta=False, id="bar")
                yield Static("Idle", id="status")
                yield RichLog(id="log", markup=True, wrap=True, highlight=False, max_lines=1000)

            with TabPane("📋  Watch", id="tab-watch"):
                yield Static("Clipboard Watch", classes="title")
                yield Static(
                    "Copy any supported video link anywhere on your PC. It is queued and downloaded\n"
                    "automatically, one at a time. Mode and quality follow the Download tab.",
                    classes="dim",
                )
                with Horizontal(classes="row"):
                    yield Switch(id="watch-switch")
                    yield Label("Clipboard Watch is OFF", id="watch-status")
                yield RichLog(id="watch-log", markup=True, wrap=True, highlight=False, max_lines=500)

            with TabPane("🔔  Subscriptions", id="tab-subs"):
                yield DataTable(id="subs-table", cursor_type="row", zebra_stripes=True)
                with Horizontal(classes="row"):
                    yield Input(placeholder="Channel or playlist URL", id="sub-url", classes="grow")
                    yield Input(placeholder="Name (optional)", id="sub-name", classes="grow")
                with Horizontal(classes="row"):
                    yield Button("Subscribe", variant="primary", id="sub-add")
                    yield Button("Sync new uploads", variant="success", id="sub-sync")
                    yield Button("Remove selected", variant="error", id="sub-remove")

            with TabPane("🕘  History", id="tab-history"):
                yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
                with Horizontal(classes="row"):
                    yield Button("Refresh", id="hist-refresh")

            with TabPane("⚙  Settings", id="tab-settings"):
                with Horizontal(classes="row"):
                    yield Label("Downloads folder", classes="dim")
                yield Input(value=str(self.config.get("download_dir", "")), id="set-dir")
                with Horizontal(classes="row"):
                    yield Label("Parallel parts per file (1-16)", classes="dim")
                yield Input(value=str(self.config.get("concurrent_fragments", 4)), id="set-frag")
                with Horizontal(classes="row"):
                    yield Label("Videos at once (1-5)", classes="dim")
                yield Input(value=str(self.config.get("parallel_videos", 2)), id="set-par")
                with Horizontal(classes="row"):
                    yield Switch(value=bool(self.config.get("clean_default")), id="set-clean")
                    yield Label("Clean every download with SponsorBlock by default")
                with Horizontal(classes="row"):
                    yield Button("Save settings", variant="success", id="set-save")
        yield Footer()

    # ------------------------------------------------------------- lifecycle
    def on_mount(self) -> None:
        self.session = secrets.token_hex(4)
        self.sub_title = f"v{core.VERSION} · session 0x{self.session}"
        core._ui_log = self._bridge_log
        core._ui_progress = self._bridge_progress
        table = self.query_one("#history-table", DataTable)
        table.add_columns("Date", "Type", "Title", "File")
        subs = self.query_one("#subs-table", DataTable)
        subs.add_columns("Name", "Type", "Since", "URL")
        self._refresh_history()
        self._refresh_subs()
        threading.Thread(target=self._consume_jobs, daemon=True, name="ytd-jobs").start()
        self.push_screen(BootScreen(boot_checks(), self.session, core.VERSION), self._after_boot)

    def _after_boot(self, _result=None) -> None:
        self._log("[#33ff66]YT-DLP ready.[/#33ff66] Paste a link and press Enter.")
        self.query_one("#url", Input).focus()

    def on_unmount(self) -> None:
        core._ui_log = None
        core._ui_progress = None
        if self._watch_stop:
            self._watch_stop.set()
        self.jobs.put(None)

    # ---------------------------------------------------- thread-safe helpers
    def _call(self, fn, *args) -> None:
        """Run fn on the UI thread (directly if we are already on it)."""
        try:
            if threading.current_thread() is threading.main_thread():
                fn(*args)
            else:
                self.call_from_thread(fn, *args)
        except Exception:  # noqa: BLE001 - app may be shutting down
            pass

    def _log(self, markup: str) -> None:
        self._call(self._write_log, markup)

    def _write_log(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(_safe_text(markup))

    def _bridge_log(self, markup: str) -> None:
        self._log(markup)
        self._call(self._write_watch_log, markup)

    def _write_watch_log(self, markup: str) -> None:
        self.query_one("#watch-log", RichLog).write(_safe_text(markup))

    def _bridge_progress(self, name: str, done: int, total: int | None) -> None:
        self._call(self._set_progress, name, done, total)

    def _set_progress(self, name: str, done: int, total: int | None) -> None:
        bar = self.query_one("#bar", ProgressBar)
        bar.update(total=total or None, progress=done)
        pct = f"{done * 100 / total:.0f}%" if total else "…"
        size = f"{core.human_size(done)} / {core.human_size(total)}" if total else core.human_size(done)
        self.query_one("#status", Static).update(f"{name}   {pct}   {size}")

    def _set_status(self, text: str) -> None:
        self._call(lambda: self.query_one("#status", Static).update(text))

    def _refresh_history(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        for entry in reversed(core.load_history()[-200:]):
            table.add_row(
                entry.get("date", ""), entry.get("mode", ""),
                str(entry.get("title", ""))[:80], str(entry.get("file", "")),
            )

    def _refresh_subs(self) -> None:
        table = self.query_one("#subs-table", DataTable)
        table.clear()
        for s in core.load_subscriptions():
            table.add_row(s.get("name", ""), s.get("mode", ""), s.get("added", ""), s.get("url", ""))

    # ----------------------------------------------------------- job runner
    def _consume_jobs(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._run_job(job)
            except Exception as e:  # noqa: BLE001
                self._log(f"[red]x Error:[/red] {e}")
            finally:
                self.jobs.task_done()
                if self.jobs.empty():
                    self._set_status("Idle")
                self._call(self._refresh_history)
                self._call(self._refresh_subs)

    def _run_job(self, job: Job) -> None:
        if job.kind == "sync":
            self._log("[bold cyan]>> Syncing subscriptions[/bold cyan]")
            core.sync_subscriptions(self.downloader, self.config)
            return

        target = job.target
        if not core.looks_like_url(target):
            self._set_status(f"Searching: {target}")
            info = self.downloader.search_first(target)
            if not info:
                self._log(f"[red]x No results for:[/red] {target}")
                return
            target = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}"
            self._log(f"[bold]Top result:[/bold] {info.get('title', '?')}")

        quality = job.quality
        if job.max_bytes and job.mode == "video":
            info = self.downloader.fetch_info(target)
            if info:
                quality, est = self.downloader.pick_quality_for_size(info, job.max_bytes)
                self._log(
                    f"  Smart pick: [bold]{core.QUALITY_LABELS.get(quality, quality).strip()}[/bold] "
                    f"(~{core.human_size(est)} of max {core.human_size(job.max_bytes)})"
                )

        self._set_status(f"Starting: {target}")
        self._call(self._set_progress, "Starting…", 0, None)
        files = self.downloader.download(
            [target], mode=job.mode, quality=quality, clip=job.clip,
            sb_remove=job.sb, subs=job.subs, workers=1,
        )
        if files:
            self._log(f"[green]✓ Saved:[/green] {core.Path(files[0]).name}")
        else:
            self._log("[red]x Nothing was downloaded.[/red]")

    def _queue_download(self) -> None:
        url_box = self.query_one("#url", Input)
        text = url_box.value.strip()
        if not text:
            self.notify("Paste a link or type a search phrase first.", severity="warning")
            return
        try:
            clip = core.parse_clip(self.query_one("#clip", Input).value) if self.query_one("#clip", Input).value.strip() else None
        except ValueError as e:
            self.notify(f"Clip: {e}", severity="error")
            return
        try:
            max_bytes = core.parse_size(self.query_one("#maxsize", Input).value) if self.query_one("#maxsize", Input).value.strip() else None
        except ValueError as e:
            self.notify(f"Max size: {e}", severity="error")
            return

        job = Job(
            target=text,
            mode=self.query_one("#mode", Select).value,
            quality=self.query_one("#quality", Select).value,
            clip=clip,
            sb=core.SB_DEFAULT if self.query_one("#sb", Switch).value else None,
            subs=self.query_one("#subs", Switch).value,
            max_bytes=max_bytes,
        )
        self.jobs.put(job)
        self._queued_urls.update(u.rstrip(".,;:!?") for u in core.URL_RE.findall(text))
        self._log(f"[dim]+ queued:[/dim] {text}")
        url_box.value = ""
        self._set_status(f"Queued ({self.jobs.qsize()} waiting)")

    # -------------------------------------------------------------- watch
    def _watch_loop(self, stop: threading.Event, mode: str, quality: str) -> None:
        first = core.get_clipboard() or ""
        seen = set(core.URL_RE.findall(first))   # ignore what is already on the clipboard
        last = first
        while not stop.wait(core.WATCH_POLL_SECONDS):
            text = core.get_clipboard()
            if not text or text == last:
                continue
            last = text
            for raw in core.URL_RE.findall(text):
                url = raw.rstrip(".,;:!?")
                if url in seen or url in self._queued_urls or not core._is_supported_host(url):
                    continue
                seen.add(url)
                self._queued_urls.add(url)
                self.jobs.put(Job(target=url, mode=mode, quality=quality,
                                  sb=core.SB_DEFAULT if self.config.get("clean_default") else None))
                self._log(f"[bold green]>> Watch:[/bold green] {url}")

    def _set_watch(self, on: bool) -> None:
        if on:
            if core.get_clipboard() is None:
                self.notify(f"Clipboard unavailable. Install: {core.clipboard_hint()}", severity="error")
                self.query_one("#watch-switch", Switch).value = False
                return
            self._watch_stop = threading.Event()
            mode = self.query_one("#mode", Select).value
            quality = self.query_one("#quality", Select).value
            self._watch_thread = threading.Thread(
                target=self._watch_loop, args=(self._watch_stop, mode, quality),
                daemon=True, name="ytd-watch",
            )
            self._watch_thread.start()
            self.query_one("#watch-status", Label).update("Clipboard Watch is ON — copy a link")
            self.notify("Clipboard Watch ON")
        elif self._watch_stop:
            self._watch_stop.set()
            self._watch_stop = None
            self.query_one("#watch-status", Label).update("Clipboard Watch is OFF")
            self.notify("Clipboard Watch OFF")

    def action_toggle_watch(self) -> None:
        sw = self.query_one("#watch-switch", Switch)
        sw.value = not sw.value

    def action_focus_url(self) -> None:
        self.query_one(TabbedContent).active = "tab-download"
        self.query_one("#url", Input).focus()

    # ------------------------------------------------------------ events
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "url":
            self._queue_download()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "watch-switch":
            self._set_watch(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "go":
            self._queue_download()
        elif bid == "sub-add":
            self._subscribe()
        elif bid == "sub-sync":
            self.jobs.put(Job(kind="sync"))
            self.notify("Sync queued")
        elif bid == "sub-remove":
            self._unsubscribe()
        elif bid == "hist-refresh":
            self._refresh_history()
        elif bid == "set-save":
            self._save_settings()

    def _subscribe(self) -> None:
        url = self.query_one("#sub-url", Input).value.strip()
        if not url:
            self.notify("Enter a channel or playlist URL.", severity="warning")
            return
        name = self.query_one("#sub-name", Input).value.strip()
        mode = self.query_one("#mode", Select).value
        quality = self.query_one("#quality", Select).value
        if core.add_subscription(url, name, mode, quality):
            self.notify("Subscribed. Press 'Sync new uploads' to fetch.")
            self.query_one("#sub-url", Input).value = ""
            self.query_one("#sub-name", Input).value = ""
        else:
            self.notify("Already subscribed to that URL.", severity="warning")
        self._refresh_subs()

    def _unsubscribe(self) -> None:
        table = self.query_one("#subs-table", DataTable)
        subs = core.load_subscriptions()
        if not subs or table.cursor_row is None or table.cursor_row >= len(subs):
            self.notify("Select a subscription row first.", severity="warning")
            return
        core.remove_subscription(subs[table.cursor_row]["url"])
        self.notify("Removed.")
        self._refresh_subs()

    def _save_settings(self) -> None:
        try:
            frag = max(1, min(16, int(self.query_one("#set-frag", Input).value.strip() or 4)))
            par = max(1, min(5, int(self.query_one("#set-par", Input).value.strip() or 2)))
        except ValueError:
            self.notify("Parallel values must be numbers.", severity="error")
            return
        self.config["download_dir"] = self.query_one("#set-dir", Input).value.strip().strip('"') or self.config["download_dir"]
        self.config["concurrent_fragments"] = frag
        self.config["parallel_videos"] = par
        self.config["clean_default"] = self.query_one("#set-clean", Switch).value
        core.save_config(self.config)
        self.notify("Settings saved.")


def run_tui(config: dict) -> None:
    YtdTui(config).run()
