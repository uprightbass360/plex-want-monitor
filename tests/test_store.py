import json
from dataclasses import replace
from datetime import timedelta

from app.models import FetchLog, ItemFilter, item_to_dict


def test_upsert_inserts_then_refreshes(store, feed_items, now):
    new, seen = store.upsert_feed_items(feed_items, now)
    assert len(new) == 5 and seen == []
    later = now + timedelta(hours=1)
    new2, seen2 = store.upsert_feed_items(feed_items, later)
    assert new2 == [] and len(seen2) == 5
    item = store.get_item_by_guid("imdb://tt0080421")
    assert item.status == "review" and item.first_seen == now and item.last_seen == later


def test_upsert_accumulates_authors(store, feed_items, now):
    store.upsert_feed_items(feed_items, now)
    item = store.get_item_by_guid("imdb://tt0080421")
    other = replace(feed_items[0], author_hash="friend-c")
    store.upsert_feed_items([other], now)
    assert {a.hash for a in store.authors_for([item.id])[item.id]} == {"friend-a", "friend-c"}
    assert {a.hash: a.item_count for a in store.list_authors()}["friend-a"] == 3


def test_mark_left_feed_and_return(store, feed_items, now):
    store.upsert_feed_items(feed_items, now)
    assert store.mark_left_feed({"imdb://tt0080421"}, now) == 4
    assert store.get_item_by_guid("imdb://tt1558972").in_feed is False
    store.upsert_feed_items(feed_items, now)
    assert store.get_item_by_guid("imdb://tt1558972").in_feed is True


def test_list_items_filters_and_sorts(store, feed_items, now):
    store.upsert_feed_items(feed_items, now)
    shows = store.list_items(ItemFilter(type="show"))
    assert [i.title for i in shows] == ["Severance"]
    assert [i.title for i in store.list_items(ItemFilter(q="monte"))] == ["The Count of Monte Cristo"]
    newest = store.list_items(ItemFilter(sort="newest"))
    assert newest[0].title == "Battle Beyond the Stars"   # pub_date desc
    assert [i.title for i in store.list_items(ItemFilter(author="friend-b"))] == ["The Lord of the Rings: The War of the Rohirrim", "Severance"]


def test_set_status_records_previous_and_queue_order(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    a = store.set_status(ids[0], "queued", now); b = store.set_status(ids[1], "queued", now)
    assert (a.previous_status, a.queue_order, b.queue_order) == ("review", 1, 2)
    assert [i.id for i in store.list_items(ItemFilter(statuses=("queued",), sort="queue"))] == [ids[0], ids[1]]
    store.skip(ids[0], now)
    assert [i.id for i in store.list_items(ItemFilter(statuses=("queued",), sort="queue"))] == [ids[1], ids[0]]


def test_match_and_reject(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    store.set_match(ids[0], "123", "2", "Movies", "mach", "title", now)
    item = store.get_item(ids[0])
    assert (item.plex_rating_key, item.match_method, item.plex_machine_id) == ("123", "title", "mach")
    store.reject_rating_key(ids[0], "123"); store.clear_match(ids[0])
    item = store.get_item(ids[0])
    assert item.rejected_rating_keys == ("123",) and item.plex_rating_key is None


def test_settings_and_logs(store, now):
    assert store.get_setting("feed_interval_min", 15) == 15
    store.set_setting("selected_library_keys", ["2", "6"])
    assert store.get_setting("selected_library_keys") == ["2", "6"]
    store.log_fetch(FetchLog(started_at=now, finished_at=now, ok=False, error="boom", status_code=502))
    assert store.last_fetch().error == "boom"
    assert store.last_scan() is None


def test_item_to_dict_shape(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    d = item_to_dict(store.get_item(ids[0]), store.authors_for(ids)[ids[0]])
    assert d["search_string"] == "Battle Beyond the Stars 1980"
    assert d["authors"] == [{"hash": "friend-a", "label": None, "username": None, "display_name": None,
                             "avatar_url": None, "name": "friend"}]
    assert "token" not in json.dumps(d).lower()


def test_concurrent_threads_share_store_safely(store, feed_items, now):
    import threading

    from app.models import ItemFilter

    new, _ = store.upsert_feed_items(feed_items, now)
    errors = []

    def writer():
        try:
            for i in range(40):
                for item_id in new:
                    store.set_status(item_id, "queued" if i % 2 == 0 else "review", now)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(200):
                store.list_items(ItemFilter())
                store.counts_by_status()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=f) for f in (writer, writer, reader, reader)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert not store.conn.in_transaction
    assert sum(store.counts_by_status().values()) == len(new)


def test_migration_2_on_existing_v1_database(tmp_path, now):
    from app import db

    conn = db.connect(tmp_path / "old.db")
    for stmt in db.MIGRATIONS[0].split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO authors (hash, label, first_seen, last_seen) VALUES ('h1','L','t','t')")
    assert db.migrate(conn) == len(db.MIGRATIONS) >= 2
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(authors)")}
    assert {"username", "display_name", "avatar_url", "resolved_at"} <= cols
    assert conn.execute("SELECT label, username FROM authors").fetchone()["label"] == "L"
    conn.close()


def test_set_author_identity_and_unresolved(store, feed_items, now):
    store.upsert_feed_items(feed_items, now)
    assert set(store.unresolved_authors()) == {"friend-a", "friend-b"}
    assert store.unresolved_authors(limit=1) == ["friend-a"]
    store.set_author_identity("friend-a", "ann", "Ann", "https://x/a.png", now)
    assert store.unresolved_authors() == ["friend-b"]
    a = {x.hash: x for x in store.list_authors()}["friend-a"]
    assert (a.username, a.display_name, a.avatar_url, a.resolved_at) == ("ann", "Ann", "https://x/a.png", now)
    assert a.name == "Ann"
    store.set_author_label("friend-b", "Bo")
    b = {x.hash: x for x in store.list_authors()}["friend-b"]
    assert b.name == "Bo"
    store.set_author_identity("friend-b", "", "", None, now)
    b = {x.hash: x for x in store.list_authors()}["friend-b"]
    assert b.username is None and b.name == "Bo"
    item = store.get_item_by_guid("imdb://tt0080421")
    d = item_to_dict(item, store.authors_for([item.id])[item.id])
    assert set(d["authors"][0]) == {"hash", "label", "username", "display_name", "avatar_url", "name"}


def test_set_status_expected_from_mismatch_raises_stale(store, feed_items, now):
    import pytest

    from app.store import StaleStatus

    ids, _ = store.upsert_feed_items(feed_items, now)
    store.set_status(ids[0], "queued", now)
    with pytest.raises(StaleStatus):
        store.set_status(ids[0], "ignored", now, expected_from="review")
    assert store.get_item(ids[0]).status == "queued"
    assert store.set_status(ids[0], "grabbed", now, expected_from="queued").status == "grabbed"


def _wl(guid, author=None):
    from app.feed import FeedItem

    return FeedItem(guid, "movie", "T", "T", 2000, None, None, (), None, None, author)


def test_source_transitions_and_source_scoped_leave(store, now):
    store.upsert_feed_items([_wl("g1", "a")], now)
    assert store.get_item_by_guid("g1").source == "feed"
    store.upsert_feed_items([_wl("g1", "o")], now, source="watchlist")
    assert store.get_item_by_guid("g1").source == "both"
    store.upsert_feed_items([_wl("g1", "a")], now)
    assert store.get_item_by_guid("g1").source == "both"
    # leaves the feed but stays on the watchlist
    store.mark_left_feed(set(), now)
    it = store.get_item_by_guid("g1")
    assert it.source == "watchlist" and it.in_feed
    # now leaves the watchlist too
    store.mark_left_feed(set(), now, source="watchlist")
    it = store.get_item_by_guid("g1")
    assert it.in_feed is False
    # returning via the feed resets source to feed
    store.upsert_feed_items([_wl("g1", "a")], now)
    assert store.get_item_by_guid("g1").source == "feed"


def test_watchlist_guid_cache_and_log_source(store, now):
    from app.models import FetchLog

    assert store.get_watchlist_cache("r1") is None
    store.set_watchlist_guid("r1", "imdb://tt1", now, now)
    assert store.get_watchlist_cache("r1") == ("imdb://tt1", now)
    store.log_fetch(FetchLog(now, now, True, source="watchlist"))
    assert store.last_fetch() is None and store.last_fetch(source="watchlist").source == "watchlist"
