# Installing on Linux (and macOS)

On Linux, cbapick4me runs in the terminal. The default is a keyboard-driven terminal UI (TUI), and there's also a fully scriptable headless mode. The desktop GUI is available too if you want it.

## 1. Prerequisites

You need **Python 3.11 or newer** and **pipx**. pipx installs Python apps into their own isolated environment and puts the command on your PATH.

| Distro | Command |
|---|---|
| Arch / Omarchy / Manjaro | `sudo pacman -S python-pipx` |
| Debian / Ubuntu / Mint | `sudo apt install pipx` |
| Fedora | `sudo dnf install pipx` |
| macOS (Homebrew) | `brew install pipx` |

Then run `pipx ensurepath` once and open a new terminal.

Check your Python version with `python3 --version`. Ubuntu 22.04 ships 3.10, which is too old; use `pipx install --python python3.11 …` after installing `python3.11`.

## 2. Install

```bash
pipx install "git+https://github.com/Sennadachi/cbapick4me"
```

To install a specific release, add `@v0.1.0` to the end of the URL.

```bash
cbapick4me --version
```

### Optional: native desktop window

`cbapick4me --gui` works without anything extra; it opens the GUI in your web browser. To get a real desktop window instead, add the `desktop` extra:

```bash
pipx install --force "cbapick4me[desktop] @ git+https://github.com/Sennadachi/cbapick4me"
```

On Linux, pywebview also needs GTK WebKit, for example `sudo pacman -S webkit2gtk-4.1 python-gobject` or `sudo apt install gir1.2-webkit2-4.1 python3-gi`. If that gets fiddly, the browser mode is fine.

## 3. Add your DigiKey API key

1. Get a key by following [api-key.md](api-key.md).
2. Run `cbapick4me`, press **Ctrl+K**, and paste the **Client ID** and **Client Secret**.
3. Pick your DigiKey site, test the key, and save.

The keys are stored in `~/.config/cbapick4me/config.toml`, readable only by you (chmod 600).

If you prefer environment variables, for example in scripts or CI, set `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` and `DIGIKEY_SITE`. They override the config file. A `.env` file in the current directory also works; see [`.env.example`](../.env.example). **Never commit `.env`.**

Next: [how to use it](usage.md#terminal-ui-linux).

## Optional: `cba2pick` fuzzy-finder launcher

`cba2pick` lets you fuzzy-find a BOM in `~/Documents` and `~/Downloads` with [fzf](https://github.com/junegunn/fzf), with a preview of each file, and opens your choice in the TUI. It needs `fzf` (`sudo pacman -S fzf` / `sudo apt install fzf`).

```bash
curl -fsSL -o ~/.local/bin/cba2pick https://raw.githubusercontent.com/Sennadachi/cbapick4me/main/scripts/cba2pick
chmod +x ~/.local/bin/cba2pick
```

```bash
cba2pick                  # pick from ~/Documents and ~/Downloads, newest first
cba2pick wayfinder        # pre-filled search; opens directly if only one file matches
cba2pick --gui            # options are passed through to cbapick4me
CBAPICK_DIRS="$HOME/Documents $HOME/Projects" cba2pick  # search other folders
```

## Updating and uninstalling

```bash
pipx upgrade cbapick4me
pipx uninstall cbapick4me
rm -r ~/.config/cbapick4me ~/.cache/cbapick4me   # optional: saved keys and search cache
```
