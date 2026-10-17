# Project context for Claude

CLI tool to manage the owner's YouTube account (playlists, search, export)
through the YouTube Data API v3. Personal tool and portfolio project for a
developer moving into Python automation / QA automation.

## Structure

- `src/ytmanager/auth.py`: OAuth flow and service creation. The only module that
  talks to Google auth.
- `src/ytmanager/client.py`: `YouTubeClient`, a thin wrapper around the API that
  returns dataclasses (`Playlist`, `Video`). Receives the service by injection.
- `src/ytmanager/export.py`: CSV export.
- `src/ytmanager/tv.py`: TV remote via the unofficial Lounge API (pyytlounge).
  Separate from the Data API: no OAuth, auth state in `tv_auth.json`.
- `src/ytmanager/cli.py`: argparse subcommands. Keep it thin: parse args, call the
  client, print.
- `tests/`: pytest with `tests/fakes.py` (fake service mimicking the
  `service.resource().method(**params).execute()` chain).

## Commands

- Install: `uv venv --python 3.12 && uv pip install -e ".[dev]"`
- Tests: `pytest`
- Run: `ytm --help`

## Conventions

- Keep it simple and readable. Small functions, type hints, dataclasses.
- Every new client method gets a test using the fakes. Tests must never call
  the real API.
- Business logic goes in `client.py` or new modules, not in `cli.py`.
- Paginate list calls with `_paginate`.
- Mind quota: `search` costs 100 units; prefer cheaper calls when possible.

## Safety

- Never commit, print, or log `client_secret.json`, `token.json` or `tv_auth.json`.
- Commands that delete or modify playlists must ask for confirmation
  (or offer a `--dry-run`) before calling the API.

## Ideas / backlog

- `copy PLAYLIST_A PLAYLIST_B`
- Import a CSV of songs: search each one and add the top result
- Experiment: `ytm tv play-playlist PLAYLIST_ID` to play a whole playlist on the
  TV in order. pyytlounge has no public method for it, so it would rely on the
  private `_command("setPlaylist", ...)` and may break on library updates.
