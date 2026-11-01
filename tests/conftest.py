import json
from pathlib import Path
from datetime import UTC, datetime, timedelta

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


class FakeClock:
    def __init__(self):
        self._now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def now(self):
        return self._now

    def advance(self, minutes=0):
        self._now += timedelta(minutes=minutes)

    __call__ = now


@pytest.fixture
def clock():
    return FakeClock()


class FakePlexTV:
    """In-memory stand-in for PlexTV."""

    def __init__(self, clock):
        from app.plex_client import Connection, PlexResource

        self.clock = clock
        self.token = None
        self.username = None
        self.resources = [
            PlexResource(
                "Marvin", "marvin-id", True, "srv-tok-marvin", "1.43.5",
                (
                    Connection("https://local.marvin:32400", True, False),
                    Connection("https://remote.marvin:49179", False, False),
                ),
            ),
            PlexResource(
                "other-server", "other-server-id", False, "srv-tok-other-server", "1.42.1",
                (Connection("https://other-server.example:32400", False, False),),
            ),
        ]

    def claim(self, token, username="s4m"):
        self.token, self.username = token, username

    def create_pin(self):
        from app.plex_client import Pin

        return Pin(415990614, "ABCD", self.clock.now() + timedelta(minutes=30))

    def check_pin(self, pin_id):
        return self.token

    def user(self, token):
        from app.plex_client import PlexUser

        return PlexUser(1, self.username or "s4m")

    def servers(self, token):
        return list(self.resources)


@pytest.fixture
def fake_plextv(clock):
    return FakePlexTV(clock)


class FakeHttp:
    """httpx.Client answering /identity with 200 for any host unless dead(uri)."""

    def __init__(self):
        self.dead_uris = set()
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def dead(self, uri):
        self.dead_uris.add(uri.rstrip("/"))

    def _handle(self, request):
        u = request.url
        base = f"{u.scheme}://{u.host}" + (f":{u.port}" if u.port else "")
        if base in self.dead_uris:
            raise httpx.ConnectError("dead")
        return httpx.Response(200, json={"MediaContainer": {}})

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.fixture
def fake_http():
    return FakeHttp()


@pytest.fixture
def make_auth(fake_plextv, fake_http, clock):
    from app.plex_auth import PlexAuth
    from app.plex_client import PlexServerClient

    def make(path):
        return PlexAuth(
            path,
            fake_plextv,
            lambda url, token: PlexServerClient(url, token, fake_http.client),
            fake_http.client,
            clock.now,
        )

    return make


@pytest.fixture
def plex_auth(make_auth, tmp_path):
    return make_auth(tmp_path / "plex.json")


@pytest.fixture
def signed_in_auth(plex_auth, fake_plextv):
    plex_auth.start_pin()
    fake_plextv.claim("tok-1", username="s4m")
    assert plex_auth.poll_pin().state == "claimed"
    return plex_auth
