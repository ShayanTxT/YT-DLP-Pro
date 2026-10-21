"""Unit tests for YT-DLP Pro. Run with:  python -m pytest -q"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yt_downloader as core  # noqa: E402


# ---------------------------------------------------------------- parsing ----
def test_parse_clip():
    assert core.parse_clip("0:32-0:41") == (32.0, 41.0)
    assert core.parse_clip("1:02:03 - 1:02:13") == (3723.0, 3733.0)
    with pytest.raises(ValueError):
        core.parse_clip("abc")
    with pytest.raises(ValueError):
        core.parse_clip("0:41-0:32")


def test_parse_size():
    assert core.parse_size("100M") == 100 * 1024 * 1024
    assert core.parse_size("1.5G") == int(1.5 * 1024**3)
    assert core.parse_size("500K") == 500 * 1024
    with pytest.raises(ValueError):
        core.parse_size("lots")


def test_url_detection():
    assert core.looks_like_url("https://youtu.be/x")
    assert not core.looks_like_url("lofi hip hop")


def test_watch_host_matching_is_exact():
    assert core._is_supported_host("https://www.youtube.com/watch?v=abc")
    assert core._is_supported_host("https://m.youtube.com/x")
    assert not core._is_supported_host("https://box.com/file")
    assert not core._is_supported_host("https://fix.com/x")


# ---------------------------------------------------------- size estimates ----
INFO = {
    "duration": 100,
    "formats": [
        {"vcodec": "avc1", "acodec": "none", "height": 1080, "filesize_approx": 50_000_000},
        {"vcodec": "avc1", "acodec": "none", "height": 720, "filesize_approx": 25_000_000},
        {"vcodec": "none", "acodec": "mp4a", "filesize_approx": 3_000_000},
    ],
}


def test_size_estimates_and_smart_pick():
    sizes = core.Downloader.estimate_quality_sizes(INFO)
    assert sizes["1080"] == 53_000_000
    assert sizes["720"] == 28_000_000
    assert core.Downloader.pick_quality_for_size(INFO, 30 * 1024 * 1024)[0] == "720"


# ------------------------------------------------------- platform hints ----
def test_hints_are_non_empty_strings():
    for text in (core.ffmpeg_hint(), core.aria2_hint(), core.clipboard_hint()):
        assert isinstance(text, str) and text


def test_gif_skips_audio_files():
    assert core.convert_to_gif("/tmp/does-not-matter.mp3") is None


# ---------------------------------------------------- storage (isolated) ----
@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path / ".ytdpro")
    monkeypatch.setattr(core, "CONFIG_FILE", tmp_path / ".ytdpro" / "config.json")
    monkeypatch.setattr(core, "HISTORY_FILE", tmp_path / ".ytdpro" / "history.json")
    monkeypatch.setattr(core, "SUBS_FILE", tmp_path / ".ytdpro" / "subscriptions.json")
    return tmp_path


def test_subscriptions_roundtrip(isolated_home):
    assert core.add_subscription("https://youtube.com/@demo", "Demo") is True
    assert core.add_subscription("https://youtube.com/@demo") is False  # duplicate blocked
    assert [s["name"] for s in core.load_subscriptions()] == ["Demo"]
    assert core.remove_subscription("Demo") is True
    assert core.load_subscriptions() == []


def test_history_and_config_survive_roundtrip(isolated_home):
    core.add_history({"title": "t", "mode": "video", "file": "", "date": "2026-01-01 00:00"})
    assert core.load_history()[0]["title"] == "t"
    core.save_config({"quality": "720"})
    assert core.load_config()["quality"] == "720"
    assert not (isolated_home / ".ytdpro" / "history.json.tmp").exists()  # atomic write cleaned up


def test_corrupt_history_is_ignored_not_fatal(isolated_home):
    core.CONFIG_DIR.mkdir(parents=True)
    core.HISTORY_FILE.write_text("{not json", encoding="utf-8")
    assert core.load_history() == []


def test_batch_file_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("# comment\n\nhttps://youtu.be/a\n  https://youtu.be/b  \n", encoding="utf-8")
    assert core.read_batch_file(str(f)) == ["https://youtu.be/a", "https://youtu.be/b"]


# --------------------------------------------------------- TUI smoke test ----
def test_tui_starts_and_quits(isolated_home):
    pytest.importorskip("textual")
    import ytd_tui

    config = dict(core.DEFAULT_CONFIG, download_dir=str(isolated_home / "dl"))

    async def run():
        app = ytd_tui.YtdTui(config)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            assert app.query_one("#url") is not None
            await pilot.press("q")

    asyncio.run(run())
