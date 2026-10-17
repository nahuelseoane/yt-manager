# yt-manager

Manage your YouTube playlists from the command line using the YouTube Data API v3.

```bash
ytm playlists                           # list your playlists
ytm export PLAYLIST_ID                  # export a playlist to exports/<id>.csv
ytm search "faroeste caboclo natiruts"  # search videos
ytm add PLAYLIST_ID VIDEO_ID            # add a video to a playlist
ytm remove-duplicates PLAYLIST_ID --dry-run  # show repeated videos
ytm remove-duplicates PLAYLIST_ID       # remove them (asks first; -y to skip)
```

## Google Cloud setup (one time, ~10 minutes)

1. Go to https://console.cloud.google.com and create a project (e.g. `yt-manager`).
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **APIs & Services → OAuth consent screen** → User type **External**.
   Fill in the app name and your email. Leave it in **Testing** mode and add
   your own Google account under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   → Application type **Desktop app**.
5. Download the JSON and save it in the project root as `client_secret.json`.
   It's already in `.gitignore`. Never commit it.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## First run

```bash
ytm playlists
```

A browser login opens the first time. After you approve, a `token.json` is
saved so you don't have to log in again (also gitignored).

**WSL note:** if the browser doesn't open automatically, copy the URL printed
in the terminal into your Windows browser. WSL2 forwards `localhost`, so the
redirect back to the script works.

## TV remote

Control the YouTube app on your TV (smart TV, Chromecast, game console...).

```bash
ytm tv pair CODE        # pair once; saves tv_auth.json (gitignored)
ytm tv play VIDEO_ID    # play a video
ytm tv pause            # also: resume, next, previous
ytm tv volume 30        # 0-100
```

To get the code, open YouTube on the TV → **Settings → Link with TV code**.
The YouTube app must be open on the TV when you send a command.

> **Warning:** this uses the YouTube Lounge API through
> [pyytlounge](https://github.com/FabioGNR/pyytlounge). It is unofficial and
> undocumented, so it may break without notice. It doesn't use the Data API or
> your Google login, and it doesn't count against your quota.

`tv_auth.json` holds the TV's login token. Treat it like `token.json`: don't
share or commit it.

## Tests

```bash
pytest
```

Tests use a fake service, so they run offline and never touch your account.

## Quota

The API gives 10,000 units per day by default. Most reads cost 1 unit,
adding to or removing from a playlist costs 50, and **a search costs 100**.
