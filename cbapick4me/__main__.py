"""Entry point. Windows → desktop GUI, everything else → terminal UI.

  cbapick4me [BOM.csv]            platform default front end
  cbapick4me --tui [BOM.csv]      terminal UI
  cbapick4me --gui                desktop GUI window
  cbapick4me --serve              host the GUI as a website
  cbapick4me --headless BOM.csv   no UI; flags only
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .headless import add_spec_args


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cbapick4me", description="Pick specific capacitors and resistors for an EasyEDA BOM.")
    p.add_argument("bom", nargs="?", help="EasyEDA BOM CSV")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tui", action="store_true", help="terminal user interface (default on Linux/macOS)")
    mode.add_argument("--gui", action="store_true", help="desktop GUI window (default on Windows)")
    mode.add_argument("--serve", action="store_true", help="serve the GUI as a web app")
    mode.add_argument("--headless", action="store_true", help="no UI, use flags")
    p.add_argument("--dry-run", action="store_true", help="headless: show searches without using the network")
    p.add_argument("--allow-nearest", action="store_true", help="headless: substitute nearest E96/E24 value for non-standard resistors")
    p.add_argument("-o", "--output", help="headless: output path (default <bom>_picked4u.csv next to input)")
    p.add_argument("--host", help="--serve: bind address (env CBAPICK_HOST, default 0.0.0.0)")
    p.add_argument("--port", type=int, help="--serve/--gui: port (env CBAPICK_PORT, default 8080)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_spec_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    # Needed by the Windows exe: NiceGUI's native window uses a child process.
    multiprocessing.freeze_support()
    a = build_parser().parse_args(argv)
    if a.headless:
        from .headless import run

        return run(a)
    if a.serve:
        from .gui.app import serve

        serve(a)
        return 0
    if a.gui or (not a.tui and sys.platform == "win32"):
        from .gui.app import desktop

        desktop(a)
        return 0
    from .tui.app import run_tui

    return run_tui(a)


if __name__ in ("__main__", "__mp_main__"):
    sys.exit(main())
