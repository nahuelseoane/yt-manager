import argparse

import pytest
from fakes import FakeResource, FakeService
from googleapiclient.errors import HttpError
from httplib2 import Response

from ytmanager.cli import cmd_remove_duplicates, format_http_error, main
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


# --- API errors handled in main() ---


def http_error(status, content, reason="Forbidden"):
    resp = Response({"status": status})
    resp.reason = reason  # Response ignores a "reason" key in the dict
    return HttpError(resp, content)


QUOTA_ERROR = http_error(
    403,
    b'{"error": {"code": 403, "message": "You have exceeded your quota.",'
    b' "errors": [{"reason": "quotaExceeded"}]}}',
)


def run_main(monkeypatch, argv, service):
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *_: service)
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return exc.value.code


def test_format_http_error_uses_status_and_api_message():
    assert format_http_error(QUOTA_ERROR) == (
        "Error: YouTube API request failed (403): You have exceeded your quota."
    )


def test_format_http_error_falls_back_to_http_reason():
    error = http_error(503, b"<html>oops</html>", reason="Service Unavailable")

    assert format_http_error(error) == (
        "Error: YouTube API request failed (503): Service Unavailable"
    )


def test_format_http_error_without_reason():
    error = http_error(500, b"", reason=None)

    assert format_http_error(error) == "Error: YouTube API request failed (500)"


def test_api_error_on_read_command_exits_cleanly(monkeypatch, capsys):
    service = FakeService(playlists=FakeResource(error=QUOTA_ERROR))

    code = run_main(monkeypatch, ["playlists"], service)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.strip() == (
        "Error: YouTube API request failed (403): You have exceeded your quota."
    )


def test_api_error_on_write_command_exits_cleanly(monkeypatch, capsys):
    resource = FakeResource(error=QUOTA_ERROR)
    service = FakeService(playlist_items=resource)

    code = run_main(monkeypatch, ["add", "PL1", "vid1"], service)

    captured = capsys.readouterr()
    assert code == 1
    assert "Added" not in captured.out
    assert "(403)" in captured.err
    assert [method for method, _ in resource.calls] == ["insert"]


def test_remove_duplicates_keeps_its_own_error_report(monkeypatch, capsys):
    items = DUPLICATED + [item("i5", "a")]
    resource = make_resource(items, delete_errors={"i4": QUOTA_ERROR})
    service = FakeService(playlist_items=resource)

    code = run_main(monkeypatch, ["remove-duplicates", "PL1", "--yes"], service)

    err = capsys.readouterr().err
    assert code == 1
    assert "Removed 1 of 3" in err
    assert "YouTube API request failed" not in err


# --- create-playlist ---


def run_create(monkeypatch, argv):
    resource = FakeResource(insert_response={"id": "PLnew"})
    monkeypatch.setattr("ytmanager.cli.build_service", lambda *_: FakeService(playlists=resource))
    main(["create-playlist", *argv])
    return resource.calls[0][1]["body"]


def test_create_playlist_is_private_by_default(monkeypatch, capsys):
    body = run_create(monkeypatch, ["Road trip"])

    assert body["status"]["privacyStatus"] == "private"
    assert body["snippet"] == {"title": "Road trip", "description": ""}
    assert capsys.readouterr().out.strip() == "Created private playlist PLnew: Road trip"


def test_create_playlist_public_with_description(monkeypatch, capsys):
    body = run_create(monkeypatch, ["Road trip", "--public", "--description", "Car songs"])

    assert body["status"]["privacyStatus"] == "public"
    assert body["snippet"]["description"] == "Car songs"
    assert "Created public playlist PLnew" in capsys.readouterr().out


def test_create_playlist_rejects_private_and_public_together(monkeypatch):
    monkeypatch.setattr(
        "ytmanager.cli.build_service", lambda *_: pytest.fail("must not call the API")
    )

    with pytest.raises(SystemExit) as exc:
        main(["create-playlist", "Road trip", "--private", "--public"])

    assert exc.value.code == 2
