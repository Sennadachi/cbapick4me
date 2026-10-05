# Installing on Windows

On Windows, cbapick4me is a single `.exe`. There's no installer, and you don't need Python. It opens as a normal desktop window.

## 1. Download

1. Go to the [**latest release**](https://github.com/Sennadachi/cbapick4me/releases/latest).
2. Under **Assets**, download `cbapick4me-vX.Y.Z-windows-x64.exe`.
3. Move it somewhere permanent, for example `C:\Users\<you>\Apps\cbapick4me.exe`. You can also make a desktop shortcut: right-click → **Send to → Desktop (create shortcut)**.

Optional: each release also has a `.sha256` file you can use to check the download. In PowerShell:

```powershell
Get-FileHash .\cbapick4me-vX.Y.Z-windows-x64.exe -Algorithm SHA256
```

The hash should match the one in the `.sha256` file.

## 2. First launch: the SmartScreen warning

The exe isn't code-signed (signing certificates cost money), so the first time you run it Windows may show **"Windows protected your PC"**. Click **More info → Run anyway**. You only need to do this once per version.

The exe is built from this repository's source by GitHub Actions; see `.github/workflows/release.yml`. If you'd rather not run a prebuilt binary, [build it yourself](development.md#building-the-windows-exe).

The window uses Microsoft Edge **WebView2**, which is already included in Windows 10 and 11. If the window is blank or doesn't open, install the "Evergreen Bootstrapper" from <https://developer.microsoft.com/microsoft-edge/webview2/>.

The first start takes a few seconds while the exe unpacks itself.

## 3. Add your DigiKey API key

1. Get a key by following [api-key.md](api-key.md).
2. In cbapick4me, click **API keys** at the top.
3. Paste the **Client ID** and **Client Secret**, and choose your DigiKey site (US, UK, DE…).
4. Click **Test DigiKey**, then **Save**.

Your settings are saved in `%APPDATA%\cbapick4me\config.toml`. That file contains your secret, so don't share it or send it to anyone.

Next: [how to use it](usage.md#gui-windows). Not using EasyEDA? Switch the BOM step to [Custom CSV](usage.md#other-eda-tools-custom-csv).

## Updating

Download the newer exe from [Releases](https://github.com/Sennadachi/cbapick4me/releases) and replace the old one. Your keys and settings are kept.

## Uninstalling

1. Delete the exe.
2. To remove your saved keys and settings, delete `%APPDATA%\cbapick4me`.
3. To remove the search cache, delete `%LOCALAPPDATA%\cbapick4me`.

## Prefer the command line on Windows?

Install Python 3.11 or newer, then `pip install pipx` and follow the [Linux guide](linux.md). Everything except `cba2pick` works the same. On Windows, `cbapick4me --tui BOM.csv` gives you the terminal UI.
