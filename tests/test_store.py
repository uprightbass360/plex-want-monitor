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
    assert d["authors"] == [{"hash": "friend-a", "label": None}]
    assert "token" not in json.dumps(d).lower()
