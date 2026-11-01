from pathlib import Path
from datetime import UTC, datetime

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
