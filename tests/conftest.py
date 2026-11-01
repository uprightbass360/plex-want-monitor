import json
from pathlib import Path
from datetime import UTC, datetime

import httpx
import pytest

from app.db import connect, migrate


@pytest.fixture
def now():
    return datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def fixture_bytes():
    return (Path(__file__).parent / "fixtures" / "friends-feed.xml").read_bytes()


@pytest.fixture
def store(conn):
    from app.store import Store

    return Store(conn)


@pytest.fixture
def feed_items(fixture_bytes):
    from app.feed import parse_feed

    return parse_feed(fixture_bytes).items


class FakeFetcher:
    def __init__(self):
        self._queue = []
        self.last_url = None
        self.last_etag = None

    def queue(self, resp):
        self._queue.append(resp)

    def fetch(self, url, etag):
        self.last_url, self.last_etag = url, etag
        if not self._queue:
            raise AssertionError("FakeFetcher queue is empty: no response queued for this fetch")
        return self._queue.pop(0)


@pytest.fixture
def fetcher():
    return FakeFetcher()


class PlexTransport:
    """Router over httpx.MockTransport serving tests/fixtures/plex by path."""

    def __init__(self):
        self.fixtures = Path(__file__).parent / "fixtures" / "plex"
        self.last_request = None
        self.requests = []
        self.token = None
        self.dead_uris = set()
        self.broken = False
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _json(self, name):
        data = (self.fixtures / name).read_bytes()
        return httpx.Response(200, content=data, headers={"Content-Type": "application/json"})

    def claim_pin(self, token):
        self.token = token

    def dead(self, uri):
        self.dead_uris.add(uri)

    def _handle(self, request):
        self.last_request = request
        self.requests.append(request)
        base = f"{request.url.scheme}://{request.url.host}" + (
            f":{request.url.port}" if request.url.port else ""
        )
        if base in self.dead_uris:
            raise httpx.ConnectError("dead")
        path = request.url.path
        if request.url.host == "plex.tv":
            if path == "/api/v2/pins" and request.method == "POST":
                return self._json("pin.json")
            if path.startswith("/api/v2/pins/"):
                body = json.loads((self.fixtures / "pin.json").read_text())
                body["authToken"] = self.token
                return httpx.Response(200, json=body)
            if path == "/api/v2/user":
                return self._json("user.json")
            if path == "/api/v2/resources":
                return self._json("resources.json")
            return httpx.Response(404)
        if self.broken:
            return httpx.Response(500)
        if request.method != "GET":
            return httpx.Response(405)
        if path == "/identity":
            return self._json("identity.json")
        if path == "/library/sections":
            return self._json("sections.json")
        if path == "/library/sections/2/all":
            return self._json("movies_all.json")
        if path == "/library/sections/3/all":
            return self._json("shows_all.json")
        return httpx.Response(404)


@pytest.fixture
def transport():
    return PlexTransport()
