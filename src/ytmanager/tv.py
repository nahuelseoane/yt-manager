"""Control the YouTube app on a TV through the unofficial Lounge API (pyytlounge).

This is separate from the Data API client: it needs no OAuth, only a pairing
code from the TV ("Link with TV code"). The functions take the API object as a
parameter so tests can pass a fake.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from pyytlounge import YtLoungeApi
from pyytlounge.event_listener import EventListener
from pyytlounge.events import NowPlayingEvent, PlaybackStateEvent
from pyytlounge.exceptions import NotConnectedException
from pyytlounge.models import State

from ytmanager.client import Video

AUTH_FILE = Path("tv_auth.json")
DEVICE_NAME = "yt-manager"  # shown on the TV as the connected remote

# A command to run once connected, e.g. `lambda api: api.pause()`.
Action = Callable[[Any], Awaitable[bool]]


class NotPairedError(Exception):
    """No saved auth state: the user has to run `ytm tv pair CODE` first."""


class InvalidAuthError(Exception):
    """The saved auth file exists but can't be loaded (old format, corrupt)."""


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


def auth_state(api) -> dict:
    # Use auth.serialize(), not store_auth_state(): in pyytlounge 3.4.0,
    # store_auth_state() returns a format that load_auth_state() can't read
    # (no "version", different key names).
    return api.auth.serialize()


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
        raise InvalidAuthError


async def pair(api, code: str, path: Path) -> str:
    """Pair with the TV, save the auth state and return the TV's name."""
    try:
        linked = await api.pair(code)
    except (KeyError, ValueError):
        # The service answers a bad code with a response that has no screen.
        linked = False
    if not linked:
        raise TVError("Pairing failed. Check the code shown on the TV and try again.")
    save_auth(path, auth_state(api))
    return api.screen_name


def use_auth(api, auth: dict) -> None:
    """Load the saved auth state into the API."""
    try:
        api.load_auth_state(auth)
    except (KeyError, ValueError, TypeError):
        raise InvalidAuthError


async def connect(api, path: Path) -> None:
    """Refresh the token, save it and connect to the TV."""
    try:
        refreshed = await api.refresh_auth()
    except (KeyError, IndexError, ValueError):
        refreshed = False
    if not refreshed:
        raise TVError("Couldn't refresh the TV login. Pair again with `ytm tv pair CODE`.")
    # The lounge token can change on refresh, so save it before going on.
    save_auth(path, auth_state(api))

    if not await api.connect():
        raise TVError("Couldn't connect to the TV. Is the YouTube app open?")


async def send(api, auth: dict, path: Path, action: Action) -> None:
    """Refresh the token, connect and run one command on the TV."""
    use_auth(api, auth)
    await connect(api, path)
    if not await action(api):
        raise TVError("The TV didn't accept the command.")


# --- play-playlist ---
#
# The TV plays one video at a time (autoplay is turned off), and this computer
# does the sequencing: it listens to TV events and sends the next video when
# the current one ends. So the terminal must stay open while it plays.

# pyytlounge calls state 0 "Buffering", but YouTube uses 0 for "ended"
# (see the comment on State in pyytlounge/models.py).
ENDED = State.Buffering
# States that show the TV has picked up the video we sent.
STARTED = {State.Playing, State.Paused, State.Starting, State.Advertisement, State.AdSkipped}

START_TIMEOUT = 30.0  # seconds to wait for a video to start, or for news after a reconnect
RETRY_DELAYS = (2, 5, 10)  # seconds before each reconnect attempt

# Errors that mean the connection to the TV is gone.
CONNECTION_ERRORS = (aiohttp.ClientError, TimeoutError, OSError, NotConnectedException)


@dataclass(frozen=True)
class Play:
    """Send video `index` to the TV."""

    index: int


@dataclass(frozen=True)
class Followed:
    """The TV moved to another video of the playlist by itself: carry on from there."""

    index: int


@dataclass(frozen=True)
class Finished:
    """The last video ended."""


@dataclass(frozen=True)
class Switched:
    """Something outside the playlist is playing on the TV."""

    video_id: str


Step = Play | Followed | Finished | Switched


class PlaylistSequencer:
    """Decides what to do next from TV events. Pure: no I/O, no async."""

    def __init__(self, video_ids: list[str]):
        self.video_ids = list(video_ids)
        self.index = 0
        self.started = False  # has the TV picked up video `index`?
        self.unconfirmed = False  # reconnected, and no news about the video yet
        self._last_id: str | None = None

    @property
    def expected(self) -> str:
        return self.video_ids[self.index]

    @property
    def waiting(self) -> bool:
        """True while a timeout applies: the video hasn't started yet, or we
        reconnected and the TV hasn't reported on it."""
        return not self.started or self.unconfirmed

    def start(self) -> Step:
        return self._go_to(0)

    def on_now_playing(self, video_id: str | None, state: State) -> Step | None:
        if not video_id:
            return None  # nothing loaded: tells us nothing
        self._last_id = video_id
        if video_id != self.expected and self.started:
            index = self._find(video_id)
            if index is None:
                return Switched(video_id)
            self.index = index
            self.unconfirmed = False
            return Followed(index)
        return self.on_state(state)

    def on_state(self, state: State) -> Step | None:
        # PlaybackStateEvent has no video id: it's about the last one reported.
        # Until the TV reports our video, events are about what it played before.
        if self._last_id != self.expected:
            return None
        self.unconfirmed = False
        if state in STARTED:
            self.started = True
        elif state == ENDED and self.started:
            return self._go_to(self.index + 1)
        return None

    def on_timeout(self) -> Step:
        """Nothing happened in time: skip the current video."""
        return self._go_to(self.index + 1)

    def on_reconnect(self) -> None:
        self.unconfirmed = True

    def _go_to(self, index: int) -> Step:
        if index >= len(self.video_ids):
            return Finished()
        self.index = index
        self.started = False
        self.unconfirmed = False
        return Play(index)

    def _find(self, video_id: str) -> int | None:
        """Position of video_id, preferring the next occurrence after the current one."""
        later = self.video_ids[self.index + 1 :]
        if video_id in later:
            return self.index + 1 + later.index(video_id)
        if video_id in self.video_ids:
            return self.video_ids.index(video_id)
        return None


@dataclass
class Progress:
    """Where playback is. The caller keeps it, to report it on Ctrl+C."""

    videos: list[Video]
    index: int = 0

    def label(self) -> str:
        return f"{self.index + 1}/{len(self.videos)}  {self.videos[self.index].title}"


class PlaybackStopped(TVError):
    """Playback stopped early. The message says why and on which video."""


@dataclass(frozen=True)
class _ConnectionLost:
    generation: int  # which subscribe task reported it
    reason: str


class _QueueListener(EventListener):
    """Puts playback events in a queue. The deciding and the commands happen in
    the main loop, so pyytlounge's event loop never waits on our requests."""

    def __init__(self, queue: asyncio.Queue, debug: Callable[[str], None] | None):
        super().__init__()
        self.queue = queue
        self.debug = debug

    def _log(self, event) -> None:
        if self.debug:
            self.debug(f"[tv] {type(event).__name__} {vars(event)}")

    async def now_playing_changed(self, event):
        self._log(event)
        self.queue.put_nowait(event)

    async def playback_state_changed(self, event):
        self._log(event)
        self.queue.put_nowait(event)

    # The rest are only shown with --debug.
    async def volume_changed(self, event):
        self._log(event)

    async def autoplay_changed(self, event):
        self._log(event)

    async def ad_state_changed(self, event):
        self._log(event)

    async def ad_playing_changed(self, event):
        self._log(event)

    async def subtitles_track_changed(self, event):
        self._log(event)

    async def autoplay_up_next_changed(self, event):
        self._log(event)

    async def playback_speed_changed(self, event):
        self._log(event)

    async def disconnected(self, event):
        self._log(event)


class _PlaylistRun:
    def __init__(self, api, path, progress, out, debug, start_timeout, retry_delays, sleep):
        self.api = api
        self.path = path
        self.progress = progress
        self.out = out
        self.debug = debug
        self.start_timeout = start_timeout
        self.retry_delays = retry_delays
        self.sleep = sleep
        self.queue: asyncio.Queue = asyncio.Queue()
        self.seq = PlaylistSequencer([v.video_id for v in progress.videos])
        self.generation = 0
        self.listen_task: asyncio.Task | None = None
        self.deadline = 0.0

    async def run(self) -> None:
        self.api.event_listener = _QueueListener(self.queue, self.debug)
        await connect(self.api, self.path)
        await self._command(lambda: self.api.set_auto_play_mode(False))
        self._listen()

        step = self.seq.start()
        while not isinstance(step, Finished):
            if isinstance(step, Switched):
                raise PlaybackStopped(
                    f"Something else is playing on the TV ({step.video_id}). "
                    f"Stopped at {self.progress.label()}."
                )
            self.progress.index = step.index
            self.out(f"▶ {self.progress.label()}")
            if isinstance(step, Play):
                video_id = self.seq.expected
                await self._command(lambda: self.api.play_video(video_id))
                self._reset_deadline()
            step = await self._next_step()

    async def _next_step(self) -> Step:
        """Read TV events until one of them changes what we should do."""
        loop = asyncio.get_running_loop()
        while True:
            timeout = max(0.0, self.deadline - loop.time()) if self.seq.waiting else None
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout)
            except TimeoutError:
                why = "no news from the TV after reconnecting" if self.seq.started else "didn't start"
                self.out(f"⏭ {self.progress.label()}  ({why} within {self.start_timeout:g}s, skipping)")
                return self.seq.on_timeout()

            step = None
            if isinstance(item, _ConnectionLost):
                if item.generation == self.generation:  # ignore reports from old tasks
                    await self._reconnect(item.reason)
            elif isinstance(item, NowPlayingEvent):
                step = self.seq.on_now_playing(item.video_id, item.state)
            elif isinstance(item, PlaybackStateEvent):
                step = self.seq.on_state(item.state)
            if step is not None:
                return step

    async def _command(self, send: Callable[[], Awaitable[bool]]) -> None:
        """Send a command. If the connection is gone, reconnect and try once more."""
        for attempt in (1, 2):
            try:
                if await send():
                    return
                lost, reason = not self.api.connected(), "session ended"
            except CONNECTION_ERRORS as e:
                lost, reason = True, f"{type(e).__name__}: {e}"
            if not lost:
                raise TVError("The TV didn't accept the command.")
            if attempt == 2:
                raise PlaybackStopped(f"Lost connection to the TV during {self.progress.label()}.")
            await self._reconnect(reason)

    async def _reconnect(self, reason: str) -> None:
        self._debug(f"connection lost: {reason}")
        await self.stop_listening()
        for attempt, delay in enumerate(self.retry_delays, 1):
            await self.sleep(delay)
            self._debug(f"reconnecting (attempt {attempt}/{len(self.retry_delays)})")
            try:
                await connect(self.api, self.path)
            except (TVError, *CONNECTION_ERRORS) as e:
                self._debug(f"reconnect failed: {e}")
                continue
            # The TV's next report says where it is. Until then, the timeout applies.
            self.seq.on_reconnect()
            self._reset_deadline()
            self._listen()
            return
        raise PlaybackStopped(
            f"Lost connection to the TV during {self.progress.label()}. "
            f"Gave up after {len(self.retry_delays)} attempts."
        )

    def _listen(self) -> None:
        self.generation += 1
        self.listen_task = asyncio.create_task(self._subscribe_loop(self.generation))

    async def _subscribe_loop(self, generation: int) -> None:
        try:
            while self.api.connected():
                await self.api.subscribe()
                # subscribe() also returns when the server ends a long poll
                # normally. Pause so a server that keeps doing that right away
                # doesn't turn this into a busy loop.
                await self.sleep(1)
            reason = "the TV ended the session"
        except Exception as e:  # any failure here means the stream is unusable
            reason = f"{type(e).__name__}: {e}"
        self.queue.put_nowait(_ConnectionLost(generation, reason))

    async def stop_listening(self) -> None:
        """Cancel the subscribe task and wait for it to finish."""
        task, self.listen_task = self.listen_task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Swallow the task's cancellation, but not our own (Ctrl+C).
            if asyncio.current_task().cancelling():
                raise

    def _reset_deadline(self) -> None:
        self.deadline = asyncio.get_running_loop().time() + self.start_timeout

    def _debug(self, message: str) -> None:
        if self.debug:
            self.debug(f"[ytm] {message}")


async def play_playlist(
    api,
    auth: dict,
    path: Path,
    progress: Progress,
    *,
    out: Callable[[str], None] = print,
    debug: Callable[[str], None] | None = None,
    start_timeout: float | None = None,
    retry_delays: tuple[float, ...] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Play `progress.videos` on the TV in order and return when the last one ends.

    Raises PlaybackStopped if the connection can't be recovered or something
    else starts playing. On cancellation (Ctrl+C) it only stops listening: it
    sends nothing to the TV.
    """
    use_auth(api, auth)
    # Defaults are read at call time, so tests can patch the module constants.
    run = _PlaylistRun(
        api,
        path,
        progress,
        out,
        debug,
        START_TIMEOUT if start_timeout is None else start_timeout,
        RETRY_DELAYS if retry_delays is None else retry_delays,
        sleep,
    )
    try:
        await run.run()
    finally:
        await run.stop_listening()
