import argparse

import pytest
from fakes import FakeResource, FakeService
from googleapiclient.errors import HttpError
from httplib2 import Response

from ytmanager.cli import cmd_remove_duplicates
from ytmanager.client import YouTubeClient


def item(item_id, video_id):
    return {"id": item_id, "snippet": {"title": video_id, "resourceId": {"videoId": video_id}}}


def make_resource(items, delete_errors=None):
    return FakeResource({None: {"items": items}}, delete_errors=delete_errors)


def run(resource, dry_run=False, yes=False):
    client = YouTubeClient(FakeService(playlist_items=resource))
    args = argparse.Namespace(playlist_id="PL1", dry_run=dry_run, yes=yes)
    cmd_remove_duplicates(client, args)


def deleted_ids(resource):
    return [params["id"] for method, params in resource.calls if method == "delete"]


DUPLICATED = [item("i1", "a"), item("i2", "b"), item("i3", "a"), item("i4", "b")]


def test_no_duplicates_deletes_nothing(capsys):
    resource = make_resource([item("i1", "a"), item("i2", "b")])

    run(resource)

    assert deleted_ids(resource) == []
    assert "No duplicates found." in capsys.readouterr().out


def test_dry_run_deletes_nothing(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("dry run must not prompt"))
    resource = make_resource(DUPLICATED)

    run(resource, dry_run=True)

    assert deleted_ids(resource) == []
    out = capsys.readouterr().out
    assert "i3" in out and "i4" in out
    assert "would remove 2" in out


def test_answering_no_deletes_nothing(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    resource = make_resource(DUPLICATED)

    run(resource)

    assert deleted_ids(resource) == []


def test_empty_answer_deletes_nothing(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    resource = make_resource(DUPLICATED)

    run(resource)

    assert deleted_ids(resource) == []


def test_answering_yes_deletes_only_duplicates(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    resource = make_resource(DUPLICATED)

    run(resource)

    assert deleted_ids(resource) == ["i3", "i4"]


def test_yes_flag_skips_prompt(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("--yes must not prompt"))
    resource = make_resource(DUPLICATED)

    run(resource, yes=True)

    assert deleted_ids(resource) == ["i3", "i4"]


def test_failed_delete_stops_and_reports_progress(capsys):
    items = DUPLICATED + [item("i5", "a")]
    error = HttpError(Response({"status": 403}), b"quotaExceeded")
    resource = make_resource(items, delete_errors={"i4": error})

    with pytest.raises(SystemExit) as exc:
        run(resource, yes=True)

    assert exc.value.code == 1
    assert deleted_ids(resource) == ["i3", "i4"]  # i5 never attempted
    assert "Removed 1 of 3" in capsys.readouterr().err
