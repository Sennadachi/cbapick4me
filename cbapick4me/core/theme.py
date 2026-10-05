"""Follow the desktop's Omarchy theme.

Omarchy writes the active theme's palette to
~/.local/state/omarchy/current/theme/colors.toml on every `omarchy theme set`.
Front ends poll ThemeWatcher.changed() and restyle when it returns a palette.
On other systems (or with CBAPICK_THEME=off) there is no palette and the
front ends keep their built-in look.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

OMARCHY_COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Palette:
    dark: bool
    background: str
    foreground: str
    accent: str
    surface: str  # cards, inputs
    panel: str  # header, footer
    muted: str
    selection: str
    red: str
    yellow: str
    green: str
    blue: str
    magenta: str
    cyan: str

    def css_vars(self) -> dict[str, str]:
        return {
            "--cba-bg": self.background,
            "--cba-fg": self.foreground,
            "--cba-accent": self.accent,
            "--cba-surface": self.surface,
            "--cba-panel": self.panel,
            "--cba-muted": self.muted,
            "--cba-selection": self.selection,
            "--cba-red": self.red,
            "--cba-yellow": self.yellow,
            "--cba-green": self.green,
            "--cba-blue": self.blue,
            # Quasar brand colours drive buttons, steppers, toggles, notifications.
            "--q-primary": self.accent,
            "--q-secondary": self.magenta,
            "--q-accent": self.cyan,
            "--q-positive": self.green,
            "--q-negative": self.red,
            "--q-warning": self.yellow,
            "--q-info": self.blue,
            "--q-dark": self.surface,
            "--q-dark-page": self.background,
        }


def colors_path() -> Path | None:
    setting = os.environ.get("CBAPICK_THEME", "").strip()
    if setting.lower() in ("off", "none", "default"):
        return None
    path = Path(setting).expanduser() if setting else OMARCHY_COLORS
    return path if path.is_file() else None


def load_palette(path: Path | None = None) -> Palette | None:
    path = path or colors_path()
    if path is None:
        return None
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    c = {k: v for k, v in raw.items() if isinstance(v, str) and _HEX.match(v)}
    if "background" not in c or "foreground" not in c:
        return None

    def pick(*names: str) -> str:
        return next((c[n] for n in names if n in c), c["foreground"])

    return Palette(
        dark=str(raw.get("mode", "dark")).lower() != "light",
        background=c["background"],
        foreground=c["foreground"],
        accent=pick("accent", "blue", "cyan"),
        surface=pick("lighter_background", "dark_background", "background"),
        panel=pick("dark_background", "darker_background", "lighter_background"),
        muted=pick("light_foreground", "muted", "dark_foreground"),
        selection=pick("selection", "lighter_background"),
        red=pick("red", "bright_red"),
        yellow=pick("yellow", "bright_yellow"),
        green=pick("green", "bright_green"),
        blue=pick("blue", "bright_blue", "accent"),
        magenta=pick("magenta", "bright_magenta", "accent"),
        cyan=pick("cyan", "bright_cyan", "accent"),
    )


class ThemeWatcher:
    """Cheap change detection: re-read colors.toml only when it was rewritten."""

    def __init__(self) -> None:
        self._sig: tuple | None = None
        self.palette: Palette | None = None

    def _signature(self) -> tuple | None:
        path = colors_path()
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        return (str(path), st.st_ino, st.st_mtime_ns, st.st_size)

    def changed(self) -> Palette | None:
        """Return the new palette if the theme changed since the last call, else None."""
        sig = self._signature()
        if sig == self._sig:
            return None
        self._sig = sig
        palette = load_palette() if sig else None
        if palette == self.palette:
            return None
        self.palette = palette
        return palette
