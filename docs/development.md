# Development

```bash
git clone https://github.com/Sennadachi/cbapick4me && cd cbapick4me
python -m venv .venv && .venv/bin/pip install -e ".[desktop,dev]"
.venv/bin/python -m pytest
.venv/bin/cbapick4me tests/fixtures/wayfinder.csv
```

The tests don't touch the network. They use `tests/fake_supplier.py` through the `fake_supplier` fixture.

`scripts/cba2pick` uses the clone's `.venv` when it exists, so to work on the app you can symlink it with `ln -s "$PWD/scripts/cba2pick" ~/.local/bin/cba2pick`.

## Layout

- `cbapick4me/core/` is UI-free: BOM I/O, parsing, specs, pricing, padding, suppliers (`suppliers/digikey.py`) and the session.
- `cbapick4me/tui/` is the Textual front end.
- `cbapick4me/gui/` is the NiceGUI front end, used for both the desktop and the web.
- `cbapick4me/headless.py` is the command-line mode.
- `packaging/` contains the Windows exe entry point and build script, plus the Dockerfile.

## Making a release

1. Bump the version in **both** `pyproject.toml` and `cbapick4me/__init__.py`.
2. Commit, then tag and push:

   ```bash
   git tag v0.2.0
   git push origin main v0.2.0
   ```

3. The [Release workflow](../.github/workflows/release.yml) then:
   1. runs the tests on Windows
   2. builds `cbapick4me-v0.2.0-windows-x64.exe` with PyInstaller (`nicegui-pack`)
   3. publishes a GitHub Release with the exe, its `.sha256` checksum, and auto-generated notes

Linux users get the new version with `pipx upgrade cbapick4me`.

To build an exe without releasing, use **Actions → Release → Run workflow**. The exe is attached to the run as an artifact.

## Building the Windows exe

PyInstaller can't cross-compile, so build on Windows with Python 3.11 or newer:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

The result is `dist\cbapick4me.exe`. Double-clicking it opens the GUI window.
