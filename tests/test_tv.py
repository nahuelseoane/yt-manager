"""TV remote tests. A fake lounge API stands in for pyytlounge: nothing here
touches the network."""

import asyncio
import gc
import json

import aiohttp
import pytest
from fakes import FakeResource, FakeService
from pyytlounge.events import NowPlayingEvent, PlaybackStateEvent
from pyytlounge.models import AuthState, State

from ytmanager import tv
from ytmanager.client import Video
from ytmanager.cli import main

# The format AuthState.serialize() produces (and deserialize() requires).
AUTH = {
    "version": 0,
    "screenId": "screen1",
    "loungeIdToken": "token1",
    "refreshToken": None,
    "expiry": None,
}
REFRESHED = {**AUTH, "loungeIdToken": "token2"}
# What pyytlounge 3.4.0's store_auth_state() returns, and what the first
# version of tv.py saved. load_auth_state() can't read it.
OLD_FORMAT = {"screenId": "screen1", "lounge_id_token": "token1", "refresh_token": None}


class FakeLoungeApi:
    """Mimics the parts of YtLoungeApi we use. Every method returns
    whatever is configured and records the calls in order.

    Auth state uses the library's real AuthState (plain data, no network),
    so saving and loading behave exactly like pyytlounge.
    """

    def __init__(self, pair=True, refresh=True, connect=True, command=True):
        self.results = {"pair": pair, "refresh": refresh, "connect": connect}
        self.command_result = command
        self.auth = AuthState()
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def pair(self, code):
        self.calls.append(("pair", code))
        self.auth.screen_id = "screen1"
        self.auth.lounge_id_token = "token1"
        return self.results["pair"]

    def load_auth_state(self, data):
        # Same as the real one: raises on data it can't read.
        self.calls.append(("load_auth_state", data))
        self.auth = AuthState()
        self.auth.deserialize(data)

    def store_auth_state(self):
        # Same shape as the real one (see OLD_FORMAT). tv.py must not use it.
        return {
            "screenId": self.auth.screen_id,
            "lounge_id_token": self.auth.lounge_id_token,
            "refresh_token": self.auth.refresh_token,
        }

    async def refresh_auth(self):
        self.calls.append(("refresh_auth",))
        self.auth.lounge_id_token = "token2"
        return self.results["refresh"]

    async def connect(self):
        self.calls.append(("connect",))
        return self.results["connect"]

    async def pause(self):
        self.calls.append(("pause",))
        return self.command_result

    async def set_auto_play_mode(self, enabled):
        self.calls.append(("set_auto_play_mode", enabled))
        return self.command_result

    async def set_volume(self, volume):
        self.calls.append(("set_volume", volume))
        return self.command_result

    @property
    def screen_name(self):
        return "Living Room TV"


def call_names(api):
    return [call[0] for call in api.calls]


# --- auth file ---


def test_save_then_load_returns_same_data(tmp_path):
    path = tmp_path / "tv_auth.json"

    tv.save_auth(path, AUTH)

    assert tv.load_auth(path) == AUTH
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_missing_file_raises_not_paired(tmp_path):
    with pytest.raises(tv.NotPairedError):
        tv.load_auth(tmp_path / "tv_auth.json")


def test_load_invalid_json_raises_invalid_auth(tmp_path):
    path = tmp_path / "tv_auth.json"
    path.write_text("not json")

    with pytest.raises(tv.InvalidAuthError):
        tv.load_auth(path)


def test_saved_pairing_can_be_loaded_back(tmp_path):
    # Regression: pair, then load the saved file the way `send` does.
    path = tmp_path / "tv_auth.json"
    asyncio.run(tv.pair(FakeLoungeApi(), "123456789012", path))

    api = FakeLoungeApi()
    asyncio.run(tv.send(api, tv.load_auth(path), path, lambda a: a.pause()))

    assert api.auth.screen_id == "screen1"
    assert call_names(api)[-1] == "pause"


# --- pair ---


def test_pair_saves_auth_state_and_returns_screen_name(tmp_path):
    path = tmp_path / "tv_auth.json"
    api = FakeLoungeApi()

    name = asyncio.run(tv.pair(api, "123456789012", path))

    assert name == "Living Room TV"
    assert api.calls == [("pair", "123456789012")]
    assert json.loads(path.read_text()) == AUTH


def test_failed_pair_saves_nothing(tmp_path):
    path = tmp_path / "tv_auth.json"

    with pytest.raises(tv.TVError, match="Pairing failed"):
        asyncio.run(tv.pair(FakeLoungeApi(pair=False), "bad", path))

    assert not path.exists()


# --- send ---


def test_send_saves_refreshed_token_then_runs_command(tmp_path):
    path = tmp_path / "tv_auth.json"
    api = FakeLoungeApi()

    asyncio.run(tv.send(api, AUTH, path, lambda a: a.pause()))

    assert call_names(api) == ["load_auth_state", "refresh_auth", "connect", "pause"]
    assert tv.load_auth(path) == REFRESHED


@pytest.mark.parametrize(
    "data", [OLD_FORMAT, {**AUTH, "version": 99}, []], ids=["old", "version", "type"]
)
def test_send_rejects_unreadable_auth(data, tmp_path):
    api = FakeLoungeApi()

    with pytest.raises(tv.InvalidAuthError):
        asyncio.run(tv.send(api, data, tmp_path / "tv_auth.json", lambda a: a.pause()))

    assert call_names(api) == ["load_auth_state"]


def test_send_raises_when_connect_fails(tmp_path):
    api = FakeLoungeApi(connect=False)

    with pytest.raises(tv.TVError, match="connect"):
        asyncio.run(tv.send(api, AUTH, tmp_path / "tv_auth.json", lambda a: a.pause()))

    assert "pause" not in call_names(api)


def test_send_raises_when_refresh_fails(tmp_path):
    path = tmp_path / "tv_auth.json"
    api = FakeLoungeApi(refresh=False)

    with pytest.raises(tv.TVError, match="Pair again"):
        asyncio.run(tv.send(api, AUTH, path, lambda a: a.pause()))

    assert call_names(api) == ["load_auth_state", "refresh_auth"]
    assert not path.exists()


def test_send_raises_when_tv_rejects_command(tmp_path):
    api = FakeLoungeApi(command=False)

    with pytest.raises(tv.TVError, match="didn't accept"):
        asyncio.run(tv.send(api, AUTH, tmp_path / "tv_auth.json", lambda a: a.pause()))


# --- CLI ---


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    path = tmp_path / "tv_auth.json"
    monkeypatch.setattr(tv, "AUTH_FILE", path)
    return path


def test_tv_command_without_pairing_exits_with_hint(auth_path, monkeypatch, capsys):
    monkeypatch.setattr(tv, "make_api", lambda: pytest.fail("must not create the API"))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "pause"])

    assert exc.value.code == 1
    assert capsys.readouterr().err.strip() == "Run `ytm tv pair CODE` first."


INVALID_MESSAGE = "Saved TV login is invalid. Run ytm tv pair CODE again."


def test_old_auth_file_exits_with_pair_again_message(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, OLD_FORMAT)
    monkeypatch.setattr(tv, "make_api", lambda: FakeLoungeApi())

    with pytest.raises(SystemExit) as exc:
        main(["tv", "play", "vid1"])

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert captured.err.strip() == INVALID_MESSAGE
    assert captured.out == ""


def test_corrupt_auth_file_exits_with_pair_again_message(auth_path, monkeypatch, capsys):
    auth_path.write_text("{broken")
    monkeypatch.setattr(tv, "make_api", lambda: pytest.fail("must not create the API"))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "pause"])

    assert exc.value.code == 1
    assert capsys.readouterr().err.strip() == INVALID_MESSAGE


def test_tv_volume_sends_level(auth_path, monkeypatch):
    tv.save_auth(auth_path, AUTH)
    api = FakeLoungeApi()
    monkeypatch.setattr(tv, "make_api", lambda: api)

    main(["tv", "volume", "30"])

    assert api.calls[-1] == ("set_volume", 30)


@pytest.mark.parametrize("level", ["-1", "101", "loud"])
def test_tv_volume_rejects_out_of_range(level, auth_path, monkeypatch):
    monkeypatch.setattr(tv, "make_api", lambda: pytest.fail("must not create the API"))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "volume", level])

    assert exc.value.code == 2  # argparse usage error


@pytest.mark.parametrize("mode, enabled", [("on", True), ("off", False)])
def test_tv_autoplay_sends_bool(mode, enabled, auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    api = FakeLoungeApi()
    monkeypatch.setattr(tv, "make_api", lambda: api)

    main(["tv", "autoplay", mode])

    assert api.calls[-1] == ("set_auto_play_mode", enabled)
    assert capsys.readouterr().out.strip() == f"Autoplay {mode}"


@pytest.mark.parametrize("mode", ["yes", "ON", "1"])
def test_tv_autoplay_rejects_other_values(mode, auth_path, monkeypatch):
    monkeypatch.setattr(tv, "make_api", lambda: pytest.fail("must not create the API"))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "autoplay", mode])

    assert exc.value.code == 2  # argparse usage error


def test_tv_autoplay_rejected_by_tv_exits_with_error(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr(tv, "make_api", lambda: FakeLoungeApi(command=False))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "autoplay", "off"])

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert captured.err.strip() == "Error: The TV didn't accept the command."
    assert captured.out == ""


def test_tv_error_exits_with_readable_message(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr(tv, "make_api", lambda: FakeLoungeApi(connect=False))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "pause"])

    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("Error: Couldn't connect to the TV")


# --- regression: only Data API commands build the Data API service ---


def test_youtube_command_still_builds_service(monkeypatch):
    from fakes import FakeService

    calls = []
    monkeypatch.setattr(
        "ytmanager.cli.build_service", lambda *a: calls.append(a) or FakeService()
    )

    main(["playlists"])

    assert len(calls) == 1


def test_tv_command_does_not_build_service(auth_path, monkeypatch):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr(tv, "make_api", lambda: FakeLoungeApi())
    monkeypatch.setattr(
        "ytmanager.cli.build_service", lambda *a: pytest.fail("TV must not use OAuth")
    )

    main(["tv", "pause"])


# --- play-playlist: sequencer (pure) ---

PLAYING, PAUSED, ENDED = State.Playing, State.Paused, tv.ENDED


def started_sequencer(ids=("a", "b", "c")):
    seq = tv.PlaylistSequencer(list(ids))
    seq.start()
    seq.on_now_playing("a", PLAYING)
    return seq


def test_sequencer_starts_with_first_video():
    assert tv.PlaylistSequencer(["a", "b"]).start() == tv.Play(0)


def test_sequencer_ignores_what_the_tv_was_playing_before():
    seq = tv.PlaylistSequencer(["a", "b"])
    seq.start()

    assert seq.on_now_playing("other", PLAYING) is None
    assert seq.on_state(ENDED) is None  # still about "other"
    assert seq.waiting


def test_sequencer_ended_before_start_is_ignored():
    seq = tv.PlaylistSequencer(["a", "b"])
    seq.start()

    assert seq.on_now_playing("a", ENDED) is None
    assert seq.waiting


def test_sequencer_moves_on_when_video_ends():
    seq = started_sequencer()

    assert not seq.waiting
    assert seq.on_state(PAUSED) is None
    assert seq.on_state(ENDED) == tv.Play(1)
    assert seq.waiting


def test_sequencer_ignores_old_video_events_after_moving_on():
    seq = started_sequencer()
    seq.on_state(ENDED)

    # Late events about "a" (and an "ended" with no id) must not skip "b".
    assert seq.on_now_playing("a", ENDED) is None
    assert seq.on_state(ENDED) is None
    assert seq.index == 1


def test_sequencer_finishes_after_last_video():
    seq = started_sequencer(["a"])

    assert seq.on_state(ENDED) == tv.Finished()


def test_sequencer_reports_video_outside_playlist():
    seq = started_sequencer()

    assert seq.on_now_playing("zzz", PLAYING) == tv.Switched("zzz")


def test_sequencer_follows_a_jump_inside_the_playlist():
    seq = started_sequencer()

    assert seq.on_now_playing("c", PLAYING) == tv.Followed(2)
    assert seq.on_state(ENDED) == tv.Finished()


def test_sequencer_jump_prefers_next_occurrence_of_repeated_video():
    seq = started_sequencer(["a", "b", "x", "a", "c"])

    assert seq.on_now_playing("a", PLAYING) is None  # still the current one
    seq.on_state(ENDED)
    seq.on_now_playing("b", PLAYING)

    assert seq.on_now_playing("a", PLAYING) == tv.Followed(3)


def test_sequencer_empty_video_id_tells_nothing():
    seq = started_sequencer()

    assert seq.on_now_playing(None, State.Stopped) is None
    assert seq.on_state(ENDED) == tv.Play(1)  # still about "a"


def test_sequencer_timeout_skips_current_video():
    seq = tv.PlaylistSequencer(["a", "b"])
    seq.start()

    assert seq.on_timeout() == tv.Play(1)
    assert seq.on_timeout() == tv.Finished()


def test_sequencer_waits_after_reconnect_until_tv_reports():
    seq = started_sequencer()
    seq.on_reconnect()

    assert seq.waiting
    seq.on_now_playing("other", PLAYING)  # not about our video
    assert seq.waiting
    seq.on_now_playing("a", PLAYING)
    assert not seq.waiting


# --- play-playlist: with a fake TV ---

DROP = "drop"  # fake TV: end the session (subscribe returns, not connected)


def now_playing(video_id, state=PLAYING):
    return NowPlayingEvent({"videoId": video_id, "state": str(state.value)})


def state_event(state):
    return PlaybackStateEvent({"currentTime": "0", "duration": "10", "state": str(state.value)})


def plays_to_the_end(video_id):
    return [now_playing(video_id), state_event(ENDED)]


class FakeTV(FakeLoungeApi):
    """A fake TV that sends events like the real one, through the listener
    set on `event_listener`.

    - `on_play(video_id)` returns what the TV does after play_video: a list of
      events, exceptions (subscribe raises them) or DROP.
    - `on_connect()` returns events delivered during connect(), as the real
      connect() does with the initial state.
    - `connect_results` (consumed in order, then True) are connect()'s results.
    """

    def __init__(self, on_play=plays_to_the_end, on_connect=list, connect_results=(), **kw):
        super().__init__(**kw)
        self.on_play = on_play
        self.on_connect = on_connect
        self.connect_results = list(connect_results)
        self.event_listener = None
        self._connected = False
        self._pending: asyncio.Queue = asyncio.Queue()

    def connected(self):
        return self._connected

    async def connect(self):
        self.calls.append(("connect",))
        self._connected = self.connect_results.pop(0) if self.connect_results else True
        if self._connected:
            for event in self.on_connect():
                await self._deliver(event)
        return self._connected

    async def play_video(self, video_id):
        self.calls.append(("play_video", video_id))
        for item in self.on_play(video_id):
            self._pending.put_nowait(item)
        return self.command_result

    async def subscribe(self):
        self.calls.append(("subscribe",))
        try:
            while True:
                item = await self._pending.get()
                if isinstance(item, Exception):
                    raise item
                if item == DROP:
                    self._connected = False
                    return
                await self._deliver(item)
        except asyncio.CancelledError:
            self.calls.append(("subscribe_cancelled",))
            raise

    async def _deliver(self, event):
        if isinstance(event, NowPlayingEvent):
            await self.event_listener.now_playing_changed(event)
        else:
            await self.event_listener.playback_state_changed(event)


VIDEOS = [Video("a", "Song A", "ch"), Video("b", "Song B", "ch"), Video("c", "Song C", "ch")]


async def no_sleep(seconds):
    await asyncio.sleep(0)


def play(api, tmp_path, videos=VIDEOS, **kw):
    """Run play_playlist against the fake; return (printed lines, progress)."""
    lines: list[str] = []
    progress = tv.Progress(list(videos))

    async def go():
        coro = tv.play_playlist(
            api, AUTH, tmp_path / "tv_auth.json", progress, out=lines.append, sleep=no_sleep, **kw
        )
        await asyncio.wait_for(coro, timeout=2)  # a bug fails the test instead of hanging

    asyncio.run(go())
    return lines, progress


def played(api):
    return [call[1] for call in api.calls if call[0] == "play_video"]


def test_plays_whole_playlist_in_order(tmp_path):
    api = FakeTV()

    lines, _ = play(api, tmp_path)

    assert played(api) == ["a", "b", "c"]
    assert lines == ["▶ 1/3  Song A", "▶ 2/3  Song B", "▶ 3/3  Song C"]


def test_turns_autoplay_off_before_playing(tmp_path):
    api = FakeTV()

    play(api, tmp_path)

    names = call_names(api)
    assert ("set_auto_play_mode", False) in api.calls
    assert names.index("set_auto_play_mode") < names.index("play_video")
    assert ("set_auto_play_mode", True) not in api.calls


def test_ignores_video_on_tv_at_start(tmp_path):
    api = FakeTV(on_connect=lambda: [now_playing("other", PLAYING), state_event(ENDED)])

    play(api, tmp_path)

    assert played(api) == ["a", "b", "c"]


def test_recovers_from_lost_connection(tmp_path):
    def on_play(video_id):
        if video_id == "b" and not api.dropped:
            api.dropped = True
            return [now_playing("b"), aiohttp.ClientConnectionError("wifi down")]
        return plays_to_the_end(video_id)

    api = FakeTV(on_play=on_play, connect_results=[True, False])  # 1st retry fails
    api.dropped = False
    # After reconnecting, the TV reports "b" playing, then it ends.
    api.on_connect = lambda: plays_to_the_end("b") if api.dropped else []

    lines, _ = play(api, tmp_path)

    assert played(api) == ["a", "b", "c"]  # "b" was not sent again
    assert lines == ["▶ 1/3  Song A", "▶ 2/3  Song B", "▶ 3/3  Song C"]
    assert call_names(api).count("connect") == 3


def test_recovers_when_tv_ends_session(tmp_path):
    def on_play(video_id):
        if video_id == "a":
            return [now_playing("a"), DROP]
        return plays_to_the_end(video_id)

    api = FakeTV(on_play=on_play)
    api.on_connect = lambda: [state_event(ENDED)] if len(played(api)) == 1 else []

    play(api, tmp_path)

    assert played(api) == ["a", "b", "c"]


def test_gives_up_after_retries_with_clear_message(tmp_path):
    def on_play(video_id):
        if video_id == "b":
            return [now_playing("b"), DROP]
        return plays_to_the_end(video_id)

    api = FakeTV(on_play=on_play, connect_results=[True, False, False, False])

    with pytest.raises(tv.PlaybackStopped) as exc:
        play(api, tmp_path, retry_delays=(0, 0, 0))

    assert str(exc.value) == (
        "Lost connection to the TV during 2/3  Song B. Gave up after 3 attempts."
    )
    assert call_names(api).count("connect") == 4  # initial + 3 retries


def test_stops_when_user_plays_something_else(tmp_path):
    def on_play(video_id):
        return [now_playing(video_id), now_playing("zzz")]

    api = FakeTV(on_play=on_play)

    with pytest.raises(tv.PlaybackStopped) as exc:
        play(api, tmp_path)

    assert str(exc.value) == (
        "Something else is playing on the TV (zzz). Stopped at 1/3  Song A."
    )
    assert played(api) == ["a"]


def test_follows_user_jumping_within_playlist(tmp_path):
    def on_play(video_id):
        if video_id == "a":
            return [now_playing("a"), now_playing("c"), state_event(ENDED)]
        return plays_to_the_end(video_id)

    api = FakeTV(on_play=on_play)

    lines, _ = play(api, tmp_path)

    assert played(api) == ["a"]
    assert lines == ["▶ 1/3  Song A", "▶ 3/3  Song C"]


def test_skips_video_that_never_starts(tmp_path):
    api = FakeTV(on_play=lambda vid: [] if vid == "a" else plays_to_the_end(vid))

    lines, _ = play(api, tmp_path, start_timeout=0.05)

    assert played(api) == ["a", "b", "c"]
    assert lines[1] == "⏭ 1/3  Song A  (didn't start within 0.05s, skipping)"


def test_skips_when_tv_is_silent_after_reconnect(tmp_path):
    def on_play(video_id):
        if video_id == "a":
            return [now_playing("a"), DROP]  # then nothing at all after reconnecting
        return plays_to_the_end(video_id)

    api = FakeTV(on_play=on_play)

    lines, _ = play(api, tmp_path, start_timeout=0.05)

    assert played(api) == ["a", "b", "c"]
    assert lines[1] == (
        "⏭ 1/3  Song A  (no news from the TV after reconnecting within 0.05s, skipping)"
    )


def test_debug_prints_raw_events(tmp_path):
    debug_lines: list[str] = []

    play(FakeTV(), tmp_path, videos=VIDEOS[:1], debug=debug_lines.append)

    assert debug_lines[0].startswith("[tv] NowPlayingEvent {'video_id': 'a'")
    assert any(line.startswith("[tv] PlaybackStateEvent") for line in debug_lines)


def test_cancel_stops_subscribe_task_cleanly(tmp_path):
    # Ctrl+C makes asyncio.run cancel the main task; this does the same.
    api = FakeTV(on_play=lambda vid: [now_playing(vid)])  # plays forever
    progress = tv.Progress(list(VIDEOS))
    loop_errors: list = []

    async def go():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: loop_errors.append(ctx))
        task = asyncio.create_task(
            tv.play_playlist(api, AUTH, tmp_path / "tv_auth.json", progress, out=lambda s: None)
        )
        while ("subscribe",) not in api.calls or not played(api):
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)  # let the "a" event arrive
        calls_before = len(api.calls)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert asyncio.all_tasks() == {asyncio.current_task()}  # nothing left pending
        return api.calls[calls_before:]

    after_cancel = asyncio.run(go())

    gc.collect()  # a pending task left behind would be reported here
    assert after_cancel == [("subscribe_cancelled",)]  # no commands sent to the TV
    assert loop_errors == []
    assert progress.index == 0


# --- play-playlist: CLI ---


def playlist_service(*video_ids):
    items = [
        {"id": f"i{v}", "snippet": {"title": f"Song {v.upper()}", "resourceId": {"videoId": v}}}
        for v in video_ids
    ]
    return FakeService(playlist_items=FakeResource({None: {"items": items}}))


def test_play_playlist_cli_prints_progress(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *a: playlist_service("a", "b"))
    monkeypatch.setattr(tv, "make_api", lambda: FakeTV())

    main(["tv", "play-playlist", "PL1"])

    assert capsys.readouterr().out.splitlines() == [
        "▶ 1/2  Song A",
        "▶ 2/2  Song B",
        "Playlist finished.",
    ]


def test_empty_playlist_exits_with_message(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *a: playlist_service())
    monkeypatch.setattr(tv, "make_api", lambda: pytest.fail("must not connect to the TV"))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "play-playlist", "PL1"])

    assert exc.value.code == 1
    assert capsys.readouterr().err.strip() == "Playlist PL1 is empty. Nothing to play."


def test_ctrl_c_prints_where_it_stopped(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *a: playlist_service("a", "b"))
    monkeypatch.setattr(tv, "make_api", lambda: FakeTV())

    async def interrupted(api, auth, path, progress, **kw):
        progress.index = 1
        raise KeyboardInterrupt

    monkeypatch.setattr(tv, "play_playlist", interrupted)

    with pytest.raises(SystemExit) as exc:
        main(["tv", "play-playlist", "PL1"])

    assert exc.value.code == 130
    assert capsys.readouterr().out.strip() == "Stopped at 2/2  Song B."


def test_lost_connection_exits_without_traceback(auth_path, monkeypatch, capsys):
    tv.save_auth(auth_path, AUTH)
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *a: playlist_service("a", "b"))
    api = FakeTV(on_play=lambda vid: [now_playing(vid), DROP], connect_results=[True] + [False] * 3)
    monkeypatch.setattr(tv, "make_api", lambda: api)
    monkeypatch.setattr(tv, "RETRY_DELAYS", (0, 0, 0))

    with pytest.raises(SystemExit) as exc:
        main(["tv", "play-playlist", "PL1"])

    assert exc.value.code == 1
    assert capsys.readouterr().err.strip() == (
        "Error: Lost connection to the TV during 1/2  Song A. Gave up after 3 attempts."
    )
