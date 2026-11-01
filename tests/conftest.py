import time
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
    c = connect(tmp_path / "test.db", check_same_thread=False)
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
        self.servers_calls = 0
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
        self.servers_calls += 1
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
        if u.path == "/library/sections":
            return httpx.Response(200, json={"MediaContainer": {"Directory": [
                {"key": "2", "type": "movie", "title": "Movies"},
                {"key": "3", "type": "show", "title": "TV Shows"},
            ]}})
        return httpx.Response(200, json={"MediaContainer": {}})

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.fixture
def fake_http():
    return FakeHttp()


class FakeCommunity:
    """In-memory PlexCommunity: users maps id -> CommunityUser; fail makes calls raise PlexError."""

    def __init__(self):
        self.users = {}
        self.fail = False
        self.user_errors = set()

    def user(self, user_id):
        from app.plex_client import PlexError
        from app.plex_community import PlexCommunityUserError

        if self.fail:
            raise PlexError("community unavailable")
        if user_id in self.user_errors:
            raise PlexCommunityUserError("community.plex.tv reported a GraphQL error")
        return self.users.get(user_id)


@pytest.fixture
def fake_community():
    return FakeCommunity()


@pytest.fixture
def make_auth(fake_plextv, fake_http, clock, fake_community):
    from app.plex_auth import PlexAuth
    from app.plex_client import PlexServerClient

    def make(path):
        return PlexAuth(
            path,
            fake_plextv,
            lambda url, token, timeout=30: PlexServerClient(url, token, fake_http.client, timeout),
            fake_http.client,
            clock.now,
            community_factory=lambda token, client_id: fake_community,
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


class FakeServerClient:
    """In-memory PlexServerClient (GET-only interface)."""

    def __init__(self, machine_id="marvin-id"):
        from app.plex_client import Library

        self._Library = Library
        self.machine_id = machine_id
        self.sections_list = []
        self.entries = {}
        self.failing = set()

    def _ensure(self, key, name, type_):
        if not any(s.key == key for s in self.sections_list):
            self.sections_list.append(self._Library(key, type_, name))
        self.entries.setdefault(key, [])

    def add_section(self, key, name, type_="movie"):
        self._ensure(key, name, type_)

    def _add(self, type_, key, name, rating_key, title, year, guids):
        from app.plex_client import PlexEntry

        self._ensure(key, name, type_)
        self.entries[key].append(PlexEntry(rating_key, title, year, type_, tuple(guids)))

    def add_movie(self, section_key, section_name, rating_key, title, year, guids):
        self._add("movie", section_key, section_name, rating_key, title, year, guids)

    def add_show(self, section_key, section_name, rating_key, title, year, guids):
        self._add("show", section_key, section_name, rating_key, title, year, guids)

    def remove(self, rating_key):
        for key in self.entries:
            self.entries[key] = [e for e in self.entries[key] if e.rating_key != rating_key]

    def fail_section(self, key):
        self.failing.add(key)

    def unfail(self, key):
        self.failing.discard(key)

    def identity(self):
        from app.plex_client import ServerIdentity

        return ServerIdentity(self.machine_id, "1.43.5")

    def sections(self):
        return list(self.sections_list)

    def list_all(self, key):
        from app.plex_client import PlexError

        if key in self.failing:
            raise PlexError(f"section {key} failed")
        return list(self.entries.get(key, []))


@pytest.fixture
def fake_server():
    return FakeServerClient("marvin-id")


@pytest.fixture
def connected_auth(make_auth, fake_plextv, fake_server, fake_http, clock, tmp_path):
    from app.plex_auth import PlexAuth

    auth = PlexAuth(
        tmp_path / "plex-connected.json",
        fake_plextv,
        lambda url, token, timeout=None: fake_server,
        fake_http.client,
        clock.now,
    )
    auth.start_pin()
    fake_plextv.claim("tok-1", username="s4m")
    assert auth.poll_pin().state == "claimed"
    auth.select_server("marvin-id")
    return auth


@pytest.fixture
def auth_signed_out(plex_auth):
    return plex_auth


@pytest.fixture
def config(tmp_path):
    from app.config import Config

    return Config(data_dir=tmp_path, port=8787, log_level="INFO")


def _client(config, store, auth, fetcher):
    from fastapi.testclient import TestClient

    from app.web.app import create_app

    return TestClient(
        create_app(config, store=store, auth=auth, fetcher=fetcher, scheduler_enabled=False)
    )


@pytest.fixture
def client(config, store, plex_auth, fetcher):
    return _client(config, store, plex_auth, fetcher)


@pytest.fixture
def connected_client(config, store, connected_auth, fetcher):
    return _client(config, store, connected_auth, fetcher)


class SlowAuth:
    """Auth-like object whose server identity() sleeps and counts calls."""

    def __init__(self):
        self.calls = 0
        outer = self

        class _Server:
            def identity(self):
                outer.calls += 1
                time.sleep(0.2)
                from app.plex_client import PlexError

                raise PlexError("slow")

        self._server = _Server()

    def server_client(self):
        return self._server


@pytest.fixture
def slow_auth():
    return SlowAuth()


@pytest.fixture
def seeded(store, fetcher, fixture_bytes, now):
    from app.poller import FetchResponse, run_fetch

    store.set_setting("feed_url", "https://rss.plex.tv/8e18-aaaa-81e5")
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    assert run_fetch(store, fetcher, now).ok
    return store
