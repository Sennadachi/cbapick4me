# Getting a DigiKey API key

cbapick4me looks parts up through DigiKey's official **Product Information v4** API. Every user needs their own free API credentials: a **Client ID** and a **Client Secret**. The app doesn't come with a shared key.

## Create the key (about 5 minutes)

1. Go to **<https://developer.digikey.com>** and **Log in** with your normal DigiKey account, or register one. It's free.
2. Open **My Apps**. You'll be asked to create an **Organization** first. Any name works, for example your own name.
3. In the organization, click **Create Production App**.
   - Use a **Production** app. A *Sandbox* app only returns fake test data.
   - **Name:** anything, for example `cbapick4me`.
   - **OAuth Callback:** `https://localhost`. cbapick4me doesn't use it, but the form requires one.
   - **Products:** tick **Product Information V4**.
4. Save. Open the app and you'll see:
   - **Client ID**: a long string of letters and numbers
   - **Client Secret**: click to show it

   Copy those two long strings, **not** the app name.

The free tier allows about **1000 requests per day**. Searches are cached for 24 hours, so a typical BOM uses a few dozen requests, and re-running it costs almost nothing.

## Put it into cbapick4me

| Where | How |
|---|---|
| GUI (Windows) | Click **API keys** at the top |
| Terminal UI | Press **Ctrl+K** |
| Environment | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, `DIGIKEY_SITE`, or a `.env` file (see [`.env.example`](../.env.example)) |

1. Paste the Client ID and the Client Secret.
2. Choose your **DigiKey site** (US, UK, DE, …). Prices come back in that site's currency: US → USD, UK → GBP, DE → EUR. You can override the currency in the same dialog.
3. Click **Test DigiKey**. It should say the key works. If it doesn't:
   - Check that you copied the ID and secret, not the app name.
   - Check that the app is a Production app with Product Information V4 ticked.
   - A brand-new app can take a minute or two to become active.
4. **Save.**

The keys are saved on your own computer only:
- Windows: `%APPDATA%\cbapick4me\config.toml`
- Linux: `~/.config/cbapick4me/config.toml`, readable only by you
- macOS: `~/Library/Application Support/cbapick4me/config.toml`

cbapick4me sends them only to DigiKey's API (`api.digikey.com`).

## Keep your key private

Your Client Secret works like a **password**. Anyone who has it can use your DigiKey developer account and burn through your daily quota. DigiKey can also suspend the account if your key is abused.

- **Don't share it.** Not with friends, coworkers, or anyone helping you debug. Everyone who uses cbapick4me can make their own key; they're free.
- **Don't paste it** into GitHub issues, Discord, forums, or chat with an AI assistant.
- **Don't show it** in screenshots, screen recordings, or screen shares. Close the API keys dialog first.
- **Don't commit it.** `.env` and `config.toml` must never go into git. This repo's `.gitignore` already ignores `.env`; be careful in your own projects.
- **Don't send someone your `config.toml`.** It contains the secret in plain text.

### If your key leaks

1. Go to <https://developer.digikey.com> → **My Apps** → your app.
2. **Regenerate** the Client Secret, or delete the app and create a new one. The old secret stops working immediately.
3. Enter the new secret in cbapick4me and click **Test DigiKey**.

### Hosting for other people

If you run the [web version](hosting.md) for others, the server's keys live in the server's `.env`, which visitors can't see. Visitors can also enter their own keys; those stay only in their browser tab's memory and are never saved on the server.
