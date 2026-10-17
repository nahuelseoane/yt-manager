"""Control the YouTube app on a TV through the unofficial Lounge API (pyytlounge).

This is separate from the Data API client: it needs no OAuth, only a pairing
code from the TV ("Link with TV code"). The functions take the API object as a
parameter so tests can pass a fake.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pyytlounge import YtLoungeApi

AUTH_FILE = Path("tv_auth.json")
DEVICE_NAME = "yt-manager"  # shown on the TV as the connected remote

# A command to run once connected, e.g. `lambda api: api.pause()`.
Action = Callable[[Any], Awaitable[bool]]


class NotPairedError(Exception):
    """No saved auth state: the user has to run `ytm tv pair CODE` first."""


class TVError(Exception):
    """A lounge step failed. The message is meant for the user."""


def _quiet_logger() -> logging.Logger:
    # pyytlounge logs full tracebacks on failure; we report errors ourselves.
    logger = logging.getLogger("ytmanager.pyytlounge")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def make_api() -> YtLoungeApi:
    return YtLoungeApi(DEVICE_NAME, logger=_quiet_logger())


def save_auth(path: Path, data: dict) -> None:
    # The file holds the lounge token, so keep it private.
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def load_auth(path: Path) -> dict:
    if not path.exists():
        raise NotPairedError
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        raise TVError(f"{path} is not valid JSON. Pair again with `ytm tv pair CODE`.")


async def pair(api, code: str, path: Path) -> str:
    """Pair with the TV, save the auth state and return the TV's name."""
    try:
        linked = await api.pair(code)
    except (KeyError, ValueError):
        # The service answers a bad code with a response that has no screen.
        linked = False
    if not linked:
        raise TVError("Pairing failed. Check the code shown on the TV and try again.")
    save_auth(path, api.store_auth_state())
    return api.screen_name


async def send(api, auth: dict, path: Path, action: Action) -> None:
    """Refresh the token, connect and run one command on the TV."""
    api.load_auth_state(auth)
    try:
        refreshed = await api.refresh_auth()
    except (KeyError, IndexError, ValueError):
        refreshed = False
    if not refreshed:
        raise TVError("Couldn't refresh the TV login. Pair again with `ytm tv pair CODE`.")
    # The lounge token can change on refresh, so save it before going on.
    save_auth(path, api.store_auth_state())

    if not await api.connect():
        raise TVError("Couldn't connect to the TV. Is the YouTube app open?")
    if not await action(api):
        raise TVError("The TV didn't accept the command.")
