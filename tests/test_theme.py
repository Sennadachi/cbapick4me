import os

from textual.widgets import DataTable

from cbapick4me.core.config import Config
from cbapick4me.core.session import Session
from cbapick4me.core.specs import Settings
from cbapick4me.core.theme import ThemeWatcher, load_palette
from cbapick4me.tui.app import CbaPickApp

EVERFOREST = """mode = "dark"
accent = "#7fbbb3"
selection = "#3d484d"
background = "#2d353b"
dark_background = "#21272c"
lighter_background = "#343f44"
foreground = "#d3c6aa"
light_foreground = "#9da9a0"
red = "#e67e80"
yellow = "#dbbc7f"
green = "#a7c080"
cyan = "#83c092"
blue = "#7fbbb3"
magenta = "#d699b6"
"""

LIGHT = """mode = "light"
accent = "#1e66f5"
background = "#eff1f5"
foreground = "#4c4f69"
red = "#d20f39"
"""


def test_load_palette(tmp_path):
    f = tmp_path / "colors.toml"
    f.write_text(EVERFOREST)
    p = load_palette(f)
    assert p.dark and p.background == "#2d353b" and p.accent == "#7fbbb3"
    assert p.surface == "#343f44" and p.panel == "#21272c"
    assert p.css_vars()["--q-primary"] == "#7fbbb3"

    f.write_text(LIGHT)
    p = load_palette(f)
    assert not p.dark and p.red == "#d20f39"
    assert p.green == "#4c4f69"  # missing colours fall back to the foreground

    f.write_text("not = [valid")
    assert load_palette(f) is None


def test_watcher_reports_only_changes(tmp_path, monkeypatch):
    f = tmp_path / "colors.toml"
    f.write_text(EVERFOREST)
    monkeypatch.setenv("CBAPICK_THEME", str(f))
    w = ThemeWatcher()
    assert w.changed().accent == "#7fbbb3"
    assert w.changed() is None
    f.write_text(LIGHT)
    os.utime(f, ns=(1, 1))  # make sure the mtime differs
    assert w.changed().dark is False


def test_watcher_off(monkeypatch):
    monkeypatch.setenv("CBAPICK_THEME", "off")
    assert ThemeWatcher().changed() is None


async def test_tui_follows_theme_live(tmp_path, monkeypatch, wayfinder_bytes):
    f = tmp_path / "colors.toml"
    f.write_text(EVERFOREST)
    monkeypatch.setenv("CBAPICK_THEME", str(f))
    bom = tmp_path / "w.csv"
    bom.write_bytes(wayfinder_bytes)
    app = CbaPickApp(Session(Config(), Settings()), bom)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.theme.startswith("omarchy-")
        assert app.current_theme.background == "#2d353b" and app.current_theme.dark

        f.write_text(LIGHT)
        os.utime(f, ns=(1, 1))
        app.follow_desktop_theme()
        await pilot.pause()
        assert app.current_theme.background == "#eff1f5" and not app.current_theme.dark
        assert len([t for t in app.available_themes if t.startswith("omarchy-")]) == 1
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert app.screen.query_one(DataTable)
