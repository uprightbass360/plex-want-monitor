import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.feed import FeedItem
from app.models import ItemFilter
from app.plex_client import PlexError
from app.plex_watchlist import PlexWatchlist, WatchlistEntry, run_watchlist_sync

FIX = Path(__file__).parent / "fixtures" / "plex"
TOKEN = "tok-secret-xyz"


def _client(handler):
    return PlexWatchlist(httpx.Client(transport=httpx.MockTransport(handler)), TOKEN, "cid-1")


def _page(entries, total, offset):
    return httpx.Response(200, json={"MediaContainer": {
        "totalSize": total, "size": len(entries), "offset": offset, "Metadata": entries}})


def test_items_paginates_and_keeps_token_in_header_only():
    all_ = json.loads((FIX / "watchlist_all.json").read_text())["MediaContainer"]["Metadata"]
    seen = []

    def handler(request):
        seen.append(request)
        start = int(request.url.params["X-Plex-Container-Start"])
        assert request.url.host == "discover.provider.plex.tv"
        assert request.url.path == "/library/sections/watchlist/all"
        return _page(all_[start:start + 2], len(all_), start)

    items = _client(handler).items()
    assert [i.title for i in items] == ["Fake Movie One", "Fake Show Two", "Fake Movie Three"]
    assert len(seen) == 2
    for r in seen:
        assert r.headers["X-Plex-Token"] == TOKEN and r.headers["X-Plex-Client-Identifier"] == "cid-1"
        assert TOKEN not in str(r.url)
    first = items[0]
    assert first == WatchlistEntry(
        "aaaaaaaaaaaaaaaaaaaa0001", "plex://movie/aaaaaaaaaaaaaaaaaaaa0001", "movie", "Fake Movie One", 2001,
        "https://metadata-static.plex.tv/fake/aaaaaaaaaaaaaaaaaaaa0001.jpg", "PG")
    assert items[1].type == "show"


def test_metadata_call():
    body = (FIX / "watchlist_metadata.json").read_bytes()
    seen = []

    def handler(request):
        seen.append(request)
        d = json.loads(body)
        if request.url.params.get("includeUserState") != "1":  # plex.tv omits watchlistedAt without it
            d["MediaContainer"]["Metadata"][0].pop("watchlistedAt", None)
        return httpx.Response(200, json=d)

    meta = _client(handler).metadata("aaaaaaaaaaaaaaaaaaaa0001")
    assert meta.guids == ("imdb://tt0000001", "tmdb://1001", "tvdb://2001")
    assert meta.watchlisted_at == datetime.fromtimestamp(1790822208, UTC)
    r = seen[0]
    assert r.url.params["includeUserState"] == "1"
    assert r.url.path == "/library/metadata/aaaaaaaaaaaaaaaaaaaa0001" and r.headers["X-Plex-Token"] == TOKEN
    assert TOKEN not in str(r.url)


def test_errors_are_plexerror_without_token():
    c = _client(lambda r: httpx.Response(500))
    with pytest.raises(PlexError) as e:
        c.items()
    assert TOKEN not in str(e.value) and "500" in str(e.value)

    def boom(request):
        raise httpx.ConnectError(f"failed {request.url} {TOKEN}")

    for call in (lambda c: c.items(), lambda c: c.metadata("x")):
        with pytest.raises(PlexError) as e:
            call(_client(boom))
        assert TOKEN not in str(e.value)
    with pytest.raises(PlexError):
        _client(lambda r: httpx.Response(200, content=b"nope")).items()
    with pytest.raises(PlexError):
        _client(lambda r: httpx.Response(200, json={"MediaContainer": {"Metadata": [{"title": "x"}]}})).items()


# ---- sync ----

AT = datetime(2026, 10, 30, tzinfo=UTC)


def _owned(fake_watchlist, signed_in_auth):
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001", "tmdb://1"],
                       content_rating="PG", watchlisted_at=AT)
    fake_watchlist.add("rk2", "Fake Show Two", 2012, "show", ["tvdb://2"], watchlisted_at=AT)
    return signed_in_auth


def test_sync_inserts_owner_items_with_owner_uuid(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert (log.ok, log.source, log.item_count, log.new_count) == (True, "watchlist", 2, 2)
    assert store.last_fetch(source="watchlist").id == log.id and store.last_fetch() is None
    it = store.get_item_by_guid("imdb://tt0000001")
    assert it.source == "watchlist" and it.status == "review" and it.in_feed
    assert it.rating == "PG" and it.pub_date == AT and it.keywords == ()
    assert [a.hash for a in store.authors_for([it.id])[it.id]] == ["owner-uuid-0001"]
    assert store.get_item_by_guid("tvdb://2").type == "show"


def test_sync_caches_metadata_guids(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    run_watchlist_sync(store, signed_in_auth, now)
    assert sorted(fake_watchlist.metadata_calls) == ["rk1", "rk2"]
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert sorted(fake_watchlist.metadata_calls) == ["rk1", "rk2"] and log.new_count == 0


def test_sync_falls_back_to_plex_guid(store, signed_in_auth, fake_watchlist, now):
    fake_watchlist.add("rk9", "Obscure", 2020, "movie", [])
    run_watchlist_sync(store, signed_in_auth, now)
    assert store.get_item_by_guid("plex://movie/rk9") is not None


def test_overlap_with_feed_collapses_to_one_row_both_sources(seeded, signed_in_auth, fake_watchlist, now):
    store = seeded
    before = len(store.list_items(ItemFilter(statuses=None)))
    fake_watchlist.add("rk3", "Severance", 2022, "show", ["imdb://tt11280740"])
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert log.new_count == 0 and len(store.list_items(ItemFilter(statuses=None))) == before
    it = store.get_item_by_guid("imdb://tt11280740")
    assert it.source == "both"
    hashes = {a.hash for a in store.authors_for([it.id])[it.id]}
    assert hashes == {"friend-b", "owner-uuid-0001"}


def test_leaving_watchlist_sets_in_feed_false_but_not_feed_items(seeded, signed_in_auth, fake_watchlist, now):
    store = seeded
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    fake_watchlist.add("rk3", "Severance", 2022, "show", ["imdb://tt11280740"])
    fake_watchlist.add("rk4", "Still Here", 2005, "movie", ["imdb://tt0000004"])  # keeps the listing non-empty
    run_watchlist_sync(store, signed_in_auth, now)
    fake_watchlist.remove("rk1")
    fake_watchlist.remove("rk3")
    run_watchlist_sync(store, signed_in_auth, now)
    assert store.get_item_by_guid("imdb://tt0000001").in_feed is False
    sev = store.get_item_by_guid("imdb://tt11280740")
    assert sev.in_feed is True and sev.source == "feed"
    assert store.get_item_by_guid("imdb://tt0080421").in_feed is True  # untouched feed item


def test_feed_run_does_not_drop_watchlist_only_items(seeded, signed_in_auth, fake_watchlist, fetcher, fixture_bytes, now):
    from app.poller import FetchResponse, run_fetch

    store = seeded
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    run_watchlist_sync(store, signed_in_auth, now)
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    store.set_setting("feed_cap_threshold", 25)
    run_fetch(store, fetcher, now)
    assert store.get_item_by_guid("imdb://tt0000001").in_feed is True


def test_failure_changes_nothing_and_triage_survives(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    run_watchlist_sync(store, signed_in_auth, now)
    it = store.get_item_by_guid("imdb://tt0000001")
    store.set_status(it.id, "queued", now)
    run_watchlist_sync(store, signed_in_auth, now)  # still present: status untouched
    assert store.get_item(it.id).status == "queued"
    fake_watchlist.fail = True
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert log.ok is False and "unavailable" in log.error and log.source == "watchlist"
    assert store.last_fetch(source="watchlist").ok is False
    assert all(i.in_feed for i in store.list_items(ItemFilter(statuses=None)))


def test_metadata_failure_aborts_without_touching_items(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    fake_watchlist.metadata_fail.add("rk2")
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert log.ok is False and store.list_items(ItemFilter(statuses=None)) == []


def test_signed_out_or_disabled_is_a_noop(store, plex_auth, signed_in_auth, fake_watchlist, now):
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    log = run_watchlist_sync(store, _signed_out(plex_auth), now)
    assert log.ok is False and log.id is None
    store.set_setting("own_watchlist_enabled", False)
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert log.ok is False and log.id is None and log.item_count == 0
    assert store.last_fetch(source="watchlist") is None and store.list_items(ItemFilter(statuses=None)) == []


def _signed_out(auth):
    auth.signout()
    return auth


def test_owner_uuid_backfilled_when_missing(store, signed_in_auth, fake_watchlist, now):
    signed_in_auth._state["uuid"] = None
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    run_watchlist_sync(store, signed_in_auth, now)
    assert signed_in_auth.status()["uuid"] == "owner-uuid-0001"


async def test_scheduler_runs_sync_after_feed_fetch_and_survives_failure(
    store, fetcher, connected_auth, fake_watchlist, fixture_bytes, now
):
    from app.poller import FetchResponse
    from app.scheduler import Scheduler

    store.set_setting("feed_url", "u")
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    s = Scheduler(store, fetcher, connected_auth, lambda: now)
    log = await s.fetch_now()
    assert log.new_count == 5 and store.last_fetch(source="watchlist").new_count == 1
    fake_watchlist.fail = True
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    log = await s.fetch_now()
    assert log.ok and store.last_fetch(source="watchlist").ok is False


async def test_watchlist_only_new_items_trigger_scan(store, fetcher, connected_auth, fake_watchlist, now):
    from app.scheduler import Scheduler

    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"])
    s = Scheduler(store, fetcher, connected_auth, lambda: now)
    log = await s.fetch_now()  # feed URL unset: feed log fails, watchlist still syncs
    assert log.ok is False and store.last_scan() is not None
    assert store.get_item_by_guid("imdb://tt0000001") is not None


def test_watchlist_sync_keeps_feed_metadata_on_both_rows(seeded, signed_in_auth, fake_watchlist, now):
    store = seeded
    before = store.get_item_by_guid("imdb://tt11280740")
    assert before.link
    fake_watchlist.add("rk3", "Severance", 2022, "show", ["imdb://tt11280740"], content_rating="X", watchlisted_at=AT)
    run_watchlist_sync(store, signed_in_auth, now)
    after = store.get_item_by_guid("imdb://tt11280740")
    assert after.source == "both"
    assert (after.link, after.keywords, after.raw_title, after.pub_date, after.rating, after.thumb) == (
        before.link, before.keywords, before.raw_title, before.pub_date, before.rating, before.thumb)


def test_empty_listing_does_not_mark_items_left(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    run_watchlist_sync(store, signed_in_auth, now)
    fake_watchlist.entries = []
    log = run_watchlist_sync(store, signed_in_auth, now)
    assert log.ok and log.item_count == 0
    assert all(i.in_feed for i in store.list_items(ItemFilter(statuses=None)))


def test_exhausted_page_loop_is_an_error(monkeypatch):
    import app.plex_watchlist as pw

    monkeypatch.setattr(pw, "MAX_PAGES", 2)
    entry = json.loads((FIX / "watchlist_all.json").read_text())["MediaContainer"]["Metadata"][:1]
    c = _client(lambda r: _page(entry, 50, int(r.url.params["X-Plex-Container-Start"])))
    with pytest.raises(PlexError):
        c.items()


@pytest.mark.parametrize("raw", [None, "soon", 0, -5, True, 10**30])
def test_metadata_missing_or_invalid_watchlisted_at_is_none(raw):
    meta = {"Guid": [{"id": "imdb://tt1"}]}
    if raw is not None:
        meta["watchlistedAt"] = raw
    c = _client(lambda r: httpx.Response(200, json={"MediaContainer": {"Metadata": [meta]}}))
    m = c.metadata("x")
    assert m.watchlisted_at is None and m.guids == ("imdb://tt1",)


def test_listing_added_at_is_never_a_watchlist_date():
    assert not hasattr(WatchlistEntry, "added_at") and "added_at" not in WatchlistEntry.__dataclass_fields__


def test_sync_keeps_pub_date_across_second_sync(store, signed_in_auth, fake_watchlist, now):
    _owned(fake_watchlist, signed_in_auth)
    run_watchlist_sync(store, signed_in_auth, now)
    run_watchlist_sync(store, signed_in_auth, now)
    assert store.get_item_by_guid("imdb://tt0000001").pub_date == AT
    assert sorted(fake_watchlist.metadata_calls) == ["rk1", "rk2"]


def test_sync_without_timestamp_has_no_pub_date_and_review_shows_seen(
        store, signed_in_auth, fake_watchlist, now, client):
    fake_watchlist.add("rk9", "Undated", 2020, "movie", ["imdb://tt9"])
    run_watchlist_sync(store, signed_in_auth, now)
    assert store.get_item_by_guid("imdb://tt9").pub_date is None
    assert "seen just now" in client.get("/review").text


def test_old_cache_without_timestamp_is_refetched_and_backfilled(store, signed_in_auth, fake_watchlist, now):
    fake_watchlist.add("rk1", "Fake Movie One", 2001, "movie", ["imdb://tt0000001"], watchlisted_at=AT)
    store.set_watchlist_guid("rk1", "imdb://tt0000001", None, now)
    run_watchlist_sync(store, signed_in_auth, now)
    assert fake_watchlist.metadata_calls == ["rk1"]
    assert store.get_watchlist_cache("rk1") == ("imdb://tt0000001", AT)
    assert store.get_item_by_guid("imdb://tt0000001").pub_date == AT


def test_sorts_fall_back_to_first_seen_for_undated_rows(store, signed_in_auth, fake_watchlist, now):
    from datetime import timedelta
    fake_watchlist.add("rkA", "Undated", 2020, "movie", ["imdb://tt9"])
    fake_watchlist.add("rkB", "Old", 2020, "movie", ["imdb://tt8"], watchlisted_at=now - timedelta(days=30))
    run_watchlist_sync(store, signed_in_auth, now)  # Undated first_seen == now: newer than Old
    assert [i.title for i in store.list_items(ItemFilter(statuses=None, sort="newest"))] == ["Undated", "Old"]
    assert [i.title for i in store.list_items(ItemFilter(statuses=None, sort="oldest"))] == ["Old", "Undated"]
