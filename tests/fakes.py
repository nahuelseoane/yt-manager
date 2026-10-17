"""Fake YouTube service that mimics the googleapiclient call chain:

    service.playlists().list(**params).execute()
"""


class FakeRequest:
    def __init__(self, response: dict, error: Exception | None = None):
        self._response = response
        self._error = error

    def execute(self) -> dict:
        if self._error:
            raise self._error
        return self._response


class FakeResource:
    """Serves pages keyed by pageToken (None = first page) and records calls.

    `delete_errors` maps an id to the exception its delete should raise.
    """

    def __init__(self, pages: dict | None = None, delete_errors: dict | None = None):
        self.pages = pages or {None: {"items": []}}
        self.delete_errors = delete_errors or {}
        self.calls: list[tuple[str, dict]] = []

    def list(self, **params):
        self.calls.append(("list", params))
        return FakeRequest(self.pages[params.get("pageToken")])

    def insert(self, **params):
        self.calls.append(("insert", params))
        return FakeRequest({})

    def delete(self, **params):
        self.calls.append(("delete", params))
        return FakeRequest({}, self.delete_errors.get(params["id"]))


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
