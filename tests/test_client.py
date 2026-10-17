from fakes import FakeResource, FakeService

from ytmanager.client import Playlist, Video, YouTubeClient


def playlist_item(video_id, title, channel=None):
    snippet = {"title": title, "resourceId": {"videoId": video_id}}
    if channel is not None:
        snippet["videoOwnerChannelTitle"] = channel
    return {"snippet": snippet}


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
