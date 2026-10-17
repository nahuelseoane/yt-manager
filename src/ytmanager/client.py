"""A small, testable wrapper around the YouTube Data API.

The client receives the service object instead of creating it, so tests can
pass a fake service and never touch the network.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Playlist:
    id: str
    title: str
    item_count: int


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    channel: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _paginate(list_method: Callable, **params) -> Iterator[dict]:
    """Yield every item across all pages of a list() call."""
    page_token = None
    while True:
        request_params = dict(params)
        if page_token:
            request_params["pageToken"] = page_token
        response = list_method(**request_params).execute()
        yield from response.get("items", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return


class YouTubeClient:
    def __init__(self, service):
        self._service = service

    def list_playlists(self) -> list[Playlist]:
        items = _paginate(
            self._service.playlists().list,
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
        )
        return [
            Playlist(
                id=item["id"],
                title=item["snippet"]["title"],
                item_count=item["contentDetails"]["itemCount"],
            )
            for item in items
        ]

    def list_playlist_videos(self, playlist_id: str) -> list[Video]:
        items = _paginate(
            self._service.playlistItems().list,
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
        )
        return [
            Video(
                video_id=item["snippet"]["resourceId"]["videoId"],
                title=item["snippet"]["title"],
                # Deleted or private videos have no owner channel.
                channel=item["snippet"].get("videoOwnerChannelTitle", ""),
            )
            for item in items
        ]

    def search_videos(self, query: str, max_results: int = 5) -> list[Video]:
        # Note: each search costs 100 quota units (daily default: 10,000).
        response = (
            self._service.search()
            .list(part="snippet", q=query, type="video", maxResults=max_results)
            .execute()
        )
        return [
            Video(
                video_id=item["id"]["videoId"],
                title=item["snippet"]["title"],
                channel=item["snippet"]["channelTitle"],
            )
            for item in response.get("items", [])
        ]

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        self._service.playlistItems().insert(part="snippet", body=body).execute()
