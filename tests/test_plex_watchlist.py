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
        datetime.fromtimestamp(1760000000, UTC), "https://metadata-static.plex.tv/fake/aaaaaaaaaaaaaaaaaaaa0001.jpg", "PG")
    assert items[1].type == "show"


def test_external_guids_from_metadata_call():
    body = (FIX / "watchlist_metadata.json").read_bytes()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    guids = _client(handler).external_guids("aaaaaaaaaaaaaaaaaaaa0001")
    assert guids == ("imdb://tt0000001", "tmdb://1001", "tvdb://2001")
    r = seen[0]
    assert r.url.path == "/library/metadata/aaaaaaaaaaaaaaaaaaaa0001" and r.headers["X-Plex-Token"] == TOKEN
    assert TOKEN not in str(r.url)


def test_errors_are_plexerror_without_token():
    c = _client(lambda r: httpx.Response(500))
    with pytest.raises(PlexError) as e:
        c.items()
    assert TOKEN not in str(e.value) and "500" in str(e.value)

    def boom(request):
        raise httpx.ConnectError(f"failed {request.url} {TOKEN}")

    for call in (lambda c: c.items(), lambda c: c.external_guids("x")):
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
                       content_rating="PG", added_at=AT)
    fake_watchlist.add("rk2", "Fake Show Two", 2012, "show", ["tvdb://2"], added_at=AT)
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
