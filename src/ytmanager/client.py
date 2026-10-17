"""A small, testable wrapper around the YouTube Data API.

The client receives the service object instead of creating it, so tests can
pass a fake service and never touch the network.
"""

import html
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


@dataclass(frozen=True)
class PlaylistItem:
    """A video's entry in a playlist. `item_id` is what delete() needs."""

    item_id: str
    video: Video


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


def _text(value: str) -> str:
    """The API returns some text HTML-escaped (e.g. "Rock &amp; Roll")."""
    return html.unescape(value)


def find_duplicates(items: list[PlaylistItem]) -> list[PlaylistItem]:
    """Return every repeated entry, keeping the first occurrence of each video."""
    seen: set[str] = set()
    duplicates = []
    for item in items:
        if item.video.video_id in seen:
            duplicates.append(item)
        else:
            seen.add(item.video.video_id)
    return duplicates


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
                title=_text(item["snippet"]["title"]),
                item_count=item["contentDetails"]["itemCount"],
            )
            for item in items
        ]

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistItem]:
        items = _paginate(
            self._service.playlistItems().list,
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
        )
        return [
            PlaylistItem(
                item_id=item["id"],
                video=Video(
                    video_id=item["snippet"]["resourceId"]["videoId"],
                    title=_text(item["snippet"]["title"]),
                    # Deleted or private videos have no owner channel.
                    channel=_text(item["snippet"].get("videoOwnerChannelTitle", "")),
                ),
            )
            for item in items
        ]

    def list_playlist_videos(self, playlist_id: str) -> list[Video]:
        return [item.video for item in self.list_playlist_items(playlist_id)]

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
                title=_text(item["snippet"]["title"]),
                channel=_text(item["snippet"]["channelTitle"]),
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

    def create_playlist(
        self, title: str, description: str = "", privacy: str = "private"
    ) -> str:
        """Create a playlist and return its ID. Costs 50 quota units."""
        body = {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        }
        response = (
            self._service.playlists().insert(part="snippet,status", body=body).execute()
        )
        return response["id"]

    def delete_playlist_item(self, item_id: str) -> None:
        # Note: each delete costs 50 quota units.
        self._service.playlistItems().delete(id=item_id).execute()
