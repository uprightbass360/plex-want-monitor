import re

import httpx

from app.models import ItemFilter
from app.poller import FetchResponse, HttpxFeedFetcher, run_fetch


def drop_items(xml: bytes, keep: int) -> bytes:
    parts = re.split(rb"(?s)(<item>.*?</item>)", xml)
    out, seen = [], 0
    for p in parts:
        if p.startswith(b"<item>"):
            seen += 1
            if seen > keep:
                continue
        out.append(p)
    return b"".join(out)


def test_first_fetch_inserts_and_stores_etag(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "https://rss.plex.tv/abc")
    fetcher.queue(FetchResponse(200, fixture_bytes, 'W/"e1"'))
    log = run_fetch(store, fetcher, now)
    assert (log.ok, log.item_count, log.new_count, log.truncated) == (True, 5, 5, False)
    assert store.get_setting("feed_etag") == 'W/"e1"' and fetcher.last_etag is None


def test_304_is_a_noop(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(200, fixture_bytes, "e"))
    run_fetch(store, fetcher, now); fetcher.queue(FetchResponse(304, None, "e"))
    log = run_fetch(store, fetcher, now)
    assert log.ok and log.item_count == 0 and fetcher.last_etag == "e"


def test_removed_items_marked_left_when_under_cap(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(200, fixture_bytes, None))
    run_fetch(store, fetcher, now)
    fetcher.queue(FetchResponse(200, drop_items(fixture_bytes, keep=2), None))
    run_fetch(store, fetcher, now)
    assert sum(1 for i in store.list_items(ItemFilter()) if not i.in_feed) == 3


def test_cap_threshold_suppresses_left_inference(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); store.set_setting("feed_cap_threshold", 5)
    fetcher.queue(FetchResponse(200, fixture_bytes, None)); run_fetch(store, fetcher, now)
    fetcher.queue(FetchResponse(200, fixture_bytes, None)); log = run_fetch(store, fetcher, now)
    assert log.truncated is True and all(i.in_feed for i in store.list_items(ItemFilter()))


def test_html_error_page_changes_nothing(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(200, fixture_bytes, None)); run_fetch(store, fetcher, now)
    fetcher.queue(FetchResponse(200, b"<html>Attention Required! | Cloudflare</html>", None))
    log = run_fetch(store, fetcher, now)
    assert log.ok is False and "parse" in log.error.lower()
    assert all(i.in_feed for i in store.list_items(ItemFilter())) and store.last_fetch().ok is False


def test_http_error_and_missing_url(store, fetcher, now):
    assert run_fetch(store, fetcher, now).error == "feed URL not set"
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(502, b"bad", None))
    log = run_fetch(store, fetcher, now)
    assert log.ok is False and log.status_code == 502


def test_status_never_touched_by_refetch(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(200, fixture_bytes, None)); run_fetch(store, fetcher, now)
    item = store.get_item_by_guid("imdb://tt0080421"); store.set_status(item.id, "queued", now)
    fetcher.queue(FetchResponse(200, fixture_bytes, None)); run_fetch(store, fetcher, now)
    assert store.get_item(item.id).status == "queued"


def test_zero_items_does_not_mark_left(store, fetcher, fixture_bytes, now):
    store.set_setting("feed_url", "u"); fetcher.queue(FetchResponse(200, fixture_bytes, None)); run_fetch(store, fetcher, now)
    fetcher.queue(FetchResponse(200, drop_items(fixture_bytes, keep=0), None))
    log = run_fetch(store, fetcher, now)
    assert log.ok and log.item_count == 0
    assert all(i.in_feed for i in store.list_items(ItemFilter()))


def test_fetcher_exception_logged_without_url(store, now):
    class Boom:
        def fetch(self, url, etag):
            raise RuntimeError(f"cannot reach {url}")

    store.set_setting("feed_url", "https://secret.example/token123")
    log = run_fetch(store, Boom(), now)
    assert log.ok is False and "RuntimeError" in log.error and "secret" not in log.error


def test_httpx_fetcher_sends_etag_and_timeout():
    seen = {}

    def handler(request):
        seen["inm"] = request.headers.get("If-None-Match")
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, content=b"x", headers={"ETag": "e2"})

    f = HttpxFeedFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    r = f.fetch("https://example.test/feed", "e1")
    assert (r.status_code, r.body, r.etag) == (200, b"x", "e2")
    assert seen["inm"] == "e1" and seen["timeout"]["read"] == 20
