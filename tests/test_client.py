from fakes import FakeResource, FakeService

from ytmanager.client import Playlist, PlaylistItem, Video, YouTubeClient, find_duplicates


def playlist_item(video_id, title, channel=None, item_id=None):
    snippet = {"title": title, "resourceId": {"videoId": video_id}}
    if channel is not None:
        snippet["videoOwnerChannelTitle"] = channel
    return {"id": item_id or f"item-{video_id}", "snippet": snippet}


def entry(item_id, video_id):
    return PlaylistItem(item_id, Video(video_id, f"title {video_id}", ""))


def test_list_playlists_reads_all_pages():
    resource = FakeResource(
        {
            None: {
                "items": [
                    {"id": "PL1", "snippet": {"title": "Reggae"}, "contentDetails": {"itemCount": 12}}
                ],
                "nextPageToken": "page2",
            },
            "page2": {
                "items": [
                    {"id": "PL2", "snippet": {"title": "Rock BR"}, "contentDetails": {"itemCount": 3}}
                ]
            },
        }
    )
    client = YouTubeClient(FakeService(playlists=resource))

    playlists = client.list_playlists()

    assert playlists == [Playlist("PL1", "Reggae", 12), Playlist("PL2", "Rock BR", 3)]
    assert resource.calls[1][1]["pageToken"] == "page2"


def test_list_playlist_videos_handles_deleted_videos():
    resource = FakeResource(
        {
            None: {
                "items": [
                    playlist_item("8Fgpk7kM1JU", "Faroeste Caboclo (Ao Vivo)", "Natiruts - Topic"),
                    playlist_item("xyz", "Deleted video"),
                ]
            }
        }
    )
    client = YouTubeClient(FakeService(playlist_items=resource))

    videos = client.list_playlist_videos("PL1")

    assert videos[0] == Video("8Fgpk7kM1JU", "Faroeste Caboclo (Ao Vivo)", "Natiruts - Topic")
    assert videos[1].channel == ""


def test_search_videos_maps_results():
    resource = FakeResource(
        {
            None: {
                "items": [
                    {
                        "id": {"videoId": "abc"},
                        "snippet": {"title": "Faroeste Caboclo", "channelTitle": "Legião Urbana"},
                    }
                ]
            }
        }
    )
    client = YouTubeClient(FakeService(search=resource))

    assert client.search_videos("faroeste caboclo") == [
        Video("abc", "Faroeste Caboclo", "Legião Urbana")
    ]


def test_add_to_playlist_sends_expected_body():
    resource = FakeResource()
    client = YouTubeClient(FakeService(playlist_items=resource))

    client.add_to_playlist("PL1", "8Fgpk7kM1JU")

    method, params = resource.calls[0]
    assert method == "insert"
    assert params["body"]["snippet"] == {
        "playlistId": "PL1",
        "resourceId": {"kind": "youtube#video", "videoId": "8Fgpk7kM1JU"},
    }


def test_video_url():
    assert Video("8Fgpk7kM1JU", "t", "c").url == "https://www.youtube.com/watch?v=8Fgpk7kM1JU"


def test_list_playlist_items_keeps_item_ids_across_pages():
    resource = FakeResource(
        {
            None: {"items": [playlist_item("a", "A", item_id="i1")], "nextPageToken": "p2"},
            "p2": {"items": [playlist_item("a", "A", item_id="i2")]},
        }
    )
    client = YouTubeClient(FakeService(playlist_items=resource))

    items = client.list_playlist_items("PL1")

    assert [i.item_id for i in items] == ["i1", "i2"]
    assert items[0].video == Video("a", "A", "")


def test_find_duplicates_with_no_duplicates():
    assert find_duplicates([entry("i1", "a"), entry("i2", "b")]) == []


def test_find_duplicates_keeps_first_occurrence():
    items = [
        entry("i1", "a"),
        entry("i2", "b"),
        entry("i3", "a"),
        entry("i4", "c"),
        entry("i5", "a"),
        entry("i6", "b"),
    ]

    assert [i.item_id for i in find_duplicates(items)] == ["i3", "i5", "i6"]


def test_delete_playlist_item_sends_item_id():
    resource = FakeResource()
    client = YouTubeClient(FakeService(playlist_items=resource))

    client.delete_playlist_item("i1")

    assert resource.calls == [("delete", {"id": "i1"})]


def test_create_playlist_defaults_to_private_and_returns_id():
    resource = FakeResource(insert_response={"id": "PLnew"})
    client = YouTubeClient(FakeService(playlists=resource))

    playlist_id = client.create_playlist("Road trip")

    assert playlist_id == "PLnew"
    method, params = resource.calls[0]
    assert method == "insert"
    assert params["part"] == "snippet,status"
    assert params["body"] == {
        "snippet": {"title": "Road trip", "description": ""},
        "status": {"privacyStatus": "private"},
    }


def test_create_playlist_sends_description_and_privacy():
    resource = FakeResource(insert_response={"id": "PLnew"})
    client = YouTubeClient(FakeService(playlists=resource))

    client.create_playlist("Road trip", "Songs for the car", "public")

    body = resource.calls[0][1]["body"]
    assert body["snippet"]["description"] == "Songs for the car"
    assert body["status"]["privacyStatus"] == "public"


ESCAPED = "Chill Study Beats &amp; Jazz | 90&#39;s Mix"
UNESCAPED = "Chill Study Beats & Jazz | 90's Mix"


def test_search_unescapes_html_in_title_and_channel():
    resource = FakeResource(
        {None: {"items": [{"id": {"videoId": "abc"}, "snippet": {"title": ESCAPED, "channelTitle": "Rock &amp; Co"}}]}}
    )
    client = YouTubeClient(FakeService(search=resource))

    assert client.search_videos("q") == [Video("abc", UNESCAPED, "Rock & Co")]


def test_playlist_items_unescape_html_in_title():
    resource = FakeResource({None: {"items": [playlist_item("abc", ESCAPED, "Rock &amp; Co")]}})
    client = YouTubeClient(FakeService(playlist_items=resource))

    assert client.list_playlist_videos("PL1") == [Video("abc", UNESCAPED, "Rock & Co")]


def test_playlists_unescape_html_in_title():
    resource = FakeResource(
        {None: {"items": [{"id": "PL1", "snippet": {"title": ESCAPED}, "contentDetails": {"itemCount": 1}}]}}
    )
    client = YouTubeClient(FakeService(playlists=resource))

    assert client.list_playlists() == [Playlist("PL1", UNESCAPED, 1)]
