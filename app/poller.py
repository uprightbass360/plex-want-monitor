from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.feed import FeedParseError, parse_feed
from app.models import FetchLog
from app.store import Store

FETCH_TIMEOUT = 20.0
DEFAULT_CAP_THRESHOLD = 25


@dataclass
class FetchResponse:
    status_code: int
    body: bytes | None
    etag: str | None


class FeedFetcher(Protocol):
    def fetch(self, url: str, etag: str | None) -> FetchResponse: ...


class HttpxFeedFetcher:
    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, url: str, etag: str | None) -> FetchResponse:
        headers = {"If-None-Match": etag} if etag else {}
        r = self.client.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        return FetchResponse(r.status_code, r.content, r.headers.get("ETag"))


def run_fetch(store: Store, fetcher: FeedFetcher, now: datetime) -> FetchLog:
    def finish(**kw) -> FetchLog:
        log = FetchLog(started_at=now, finished_at=now, **kw)
        log.id = store.log_fetch(log)
        return log

    url = store.get_setting("feed_url")
    if not url:
        return finish(ok=False, error="feed URL not set")
    try:
        resp = fetcher.fetch(url, store.get_setting("feed_etag"))
    except Exception as e:  # never echo the exception text: it may contain the URL
        return finish(ok=False, error=f"fetch failed: {type(e).__name__}")
    if resp.status_code == 304:
        return finish(ok=True, status_code=304)
    if not 200 <= resp.status_code < 300:
        return finish(ok=False, status_code=resp.status_code, error=f"HTTP {resp.status_code}")
    try:
        feed = parse_feed(resp.body or b"")
    except FeedParseError as e:
        return finish(ok=False, status_code=resp.status_code, error=f"feed parse error: {e}")

    new, seen = store.upsert_feed_items(feed.items, now)
    if resp.etag:
        store.set_setting("feed_etag", resp.etag)
    count = len(feed.items)
    threshold = store.get_setting("feed_cap_threshold", DEFAULT_CAP_THRESHOLD)
    truncated = False
    if count == 0:
        pass
    elif count < threshold:
        store.mark_left_feed({i.guid for i in feed.items}, now)
    else:
        truncated = True
    return finish(ok=True, status_code=resp.status_code, item_count=count,
                  new_count=len(new), truncated=truncated)
