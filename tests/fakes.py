"""Fake YouTube service that mimics the googleapiclient call chain:

    service.playlists().list(**params).execute()
"""


class FakeRequest:
    def __init__(self, response: dict):
        self._response = response

    def execute(self) -> dict:
        return self._response


class FakeResource:
    """Serves pages keyed by pageToken (None = first page) and records calls."""

    def __init__(self, pages: dict | None = None):
        self.pages = pages or {None: {"items": []}}
        self.calls: list[tuple[str, dict]] = []

    def list(self, **params):
        self.calls.append(("list", params))
        return FakeRequest(self.pages[params.get("pageToken")])

    def insert(self, **params):
        self.calls.append(("insert", params))
        return FakeRequest({})


class FakeService:
    def __init__(self, playlists=None, playlist_items=None, search=None):
        self._playlists = playlists or FakeResource()
        self._playlist_items = playlist_items or FakeResource()
        self._search = search or FakeResource()

    def playlists(self):
        return self._playlists

    def playlistItems(self):  # noqa: N802 (matches the real API name)
        return self._playlist_items

    def search(self):
        return self._search
