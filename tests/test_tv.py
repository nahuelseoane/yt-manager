"""TV remote tests. A fake lounge API stands in for pyytlounge: nothing here
touches the network."""

import asyncio
import json

import pytest
from pyytlounge.models import AuthState

from ytmanager import tv
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
