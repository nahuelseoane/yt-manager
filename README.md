# yt-manager

[![tests](https://github.com/nahuelseoane/yt-manager/actions/workflows/tests.yml/badge.svg)](https://github.com/nahuelseoane/yt-manager/actions/workflows/tests.yml)

A Python CLI to manage your YouTube playlists and control the YouTube app on your TV from the terminal.

## Features

- **Playlists**: list your playlists with their IDs and video counts.
- **Export**: save a playlist to CSV (title, channel, URL).
- **Search**: find videos by text.
- **Add**: add a video to a playlist.
- **Remove duplicates**: find repeated videos in a playlist and remove them, with `--dry-run` to preview and a confirmation prompt before anything is deleted.
- **TV remote**: pair with your TV and play, pause, resume, skip and set the volume.

## Demo

```console
$ ytm playlists
PLx3kQ9vT2bWnR7mYcJ4dLpE8sAfHuG1zK      42  Road trip
PLm8RtY2cVnB5qWxJ7kLpZ3dFgH9sA4eTu     118  Brazilian rock

$ ytm search "faroeste caboclo" --max 2
Fq3bW8vYkTs  Faroeste Caboclo  (Legião Urbana)
x7KpLm2QnRa  Faroeste Caboclo (Ao Vivo)  (Legião Urbana)

$ ytm remove-duplicates PLm8RtY2cVnB5qWxJ7kLpZ3dFgH9sA4eTu --dry-run
UExtOFJ0WTJjVm5CNXFXeEo3a0xwWjNk  Fq3bW8vYkTs  Faroeste Caboclo
UExtOFJ0WTJjVm5CNXFXeEo3a0xwWjRl  b2NcR5tYwQe  Tempo Perdido
Dry run: would remove 2 item(s).

$ ytm remove-duplicates PLm8RtY2cVnB5qWxJ7kLpZ3dFgH9sA4eTu
UExtOFJ0WTJjVm5CNXFXeEo3a0xwWjNk  Fq3bW8vYkTs  Faroeste Caboclo
UExtOFJ0WTJjVm5CNXFXeEo3a0xwWjRl  b2NcR5tYwQe  Tempo Perdido
Quota: this uses 100 units (50 per delete).
Remove 2 duplicate(s)? [y/N] y
Removed 2 duplicate(s).

$ ytm export PLx3kQ9vT2bWnR7mYcJ4dLpE8sAfHuG1zK
Exported 42 videos to exports/PLx3kQ9vT2bWnR7mYcJ4dLpE8sAfHuG1zK.csv

$ ytm tv play Fq3bW8vYkTs
Playing Fq3bW8vYkTs
$ ytm tv volume 30
Volume set to 30
```

All commands:

```bash
ytm playlists
ytm export PLAYLIST_ID [-o FILE]            # default: exports/<id>.csv
ytm search "QUERY" [--max N]                # default: 5 results
ytm add PLAYLIST_ID VIDEO_ID
ytm remove-duplicates PLAYLIST_ID [--dry-run] [-y]
ytm tv pair CODE
ytm tv play VIDEO_ID
ytm tv pause | resume | next | previous
ytm tv volume N                             # 0-100
```

## Tech & design highlights

- **YouTube Data API v3 + OAuth 2.0** (installed-app flow). The token is cached in `token.json`, and paginated list calls go through a single helper.
- **Dependency injection for testability**: `YouTubeClient` receives the API service, and the TV functions receive the lounge API object, so tests can pass in fakes.
- **Tests with fakes, no network**: a fake service mimics the `service.resource().method(**params).execute()` chain, and a fake lounge API uses the library's real auth data class. 46 pytest tests run offline.
- **Safe destructive operations**: `remove-duplicates` offers `--dry-run`, shows the quota cost and asks before deleting. If one delete fails partway through, it stops and reports how many were removed.
- **Readable error handling**: API errors, network errors and invalid saved logins become one-line messages with exit code 1, not tracebacks.
- **Async TV control** through the unofficial YouTube Lounge API ([pyytlounge](https://github.com/FabioGNR/pyytlounge)), run with `asyncio.run`. It lives in its own module, separate from the Data API client, and TV commands never start the Google login.

## Found along the way

In pyytlounge 3.4.0, `store_auth_state()` returns `{screenId, lounge_id_token, refresh_token}`, but `load_auth_state()` expects `{version, screenId, loungeIdToken, refreshToken, expiry}`. Loading the saved state fails with `KeyError: 'version'` on the first command after pairing.

The unit tests missed it because the fake accepted any dict. The fix:

- Save `api.auth.serialize()` instead, which is the exact inverse of what `load_auth_state()` reads.
- Make the test fake use the library's real `AuthState` (plain data, no network), so a format mismatch now fails the tests.
- Show a clear "pair again" message when the saved login file is unreadable.

## Setup

### Google Cloud (one time, ~10 minutes)

1. Go to https://console.cloud.google.com and create a project (e.g. `yt-manager`).
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **APIs & Services → OAuth consent screen** → User type **External**.
   Fill in the app name and your email. Leave it in **Testing** mode and add
   your own Google account under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   → Application type **Desktop app**.
5. Download the JSON and save it in the project root as `client_secret.json`.
   It's already in `.gitignore`. Never commit it.

### Install (with [uv](https://docs.astral.sh/uv/), Python 3.12)

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### First run

```bash
ytm playlists
```

A browser login opens the first time. After you approve, a `token.json` is
saved so you don't have to log in again (also gitignored).

**WSL note:** if the browser doesn't open automatically, copy the URL printed
in the terminal into your Windows browser. WSL2 forwards `localhost`, so the
redirect back to the script works.

### TV pairing

Open YouTube on the TV → **Settings → Link with TV code**, then:

```bash
ytm tv pair CODE
```

This saves `tv_auth.json` (gitignored). It holds the TV's login token, so
treat it like `token.json`: don't share or commit it. The YouTube app must be
open on the TV when you send a command.

## Running tests

```bash
pytest
```

Tests use fakes, so they run offline and never touch your account or your TV.

## Limitations

- **The TV remote uses an unofficial API.** The YouTube Lounge API is
  undocumented, so it may break without notice. pyytlounge is pinned to `<4`
  for that reason. It doesn't use the Data API or count against your quota.
- **Quota.** The Data API gives 10,000 units per day by default. Most reads
  cost 1 unit, adding to or removing from a playlist costs 50, and **a search
  costs 100**.
