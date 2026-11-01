from app.lifecycle import apply_not_a_match
from app.scanner import run_scan


def test_not_connected(store, auth_signed_out, now):
    log = run_scan(store, auth_signed_out, now)
    assert log.ok is False and log.error == "Plex not connected"
    assert store.last_scan().error == "Plex not connected"


def test_scan_matches_from_any_status_including_ignored(store, feed_items, connected_auth, fake_server, now):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, ["imdb://tt1558972"])
    fake_server.add_movie("2", "Movies", "901", "The Count of Monte Cristo", 2024, [])
    store.set_status(store.get_item_by_guid("imdb://tt1558972").id, "ignored", now)
    log = run_scan(store, connected_auth, now)
    assert log.ok and log.matched == 2 and log.indexed == 2
    a = store.get_item_by_guid("imdb://tt1558972")
    b = store.get_item_by_guid("imdb://tt26446278")
    assert (a.status, a.match_method, a.previous_status) == ("library", "guid", "ignored")
    assert (b.status, b.match_method) == ("library", "title")


def test_revert_only_when_library_listed_on_same_server(store, feed_items, connected_auth, fake_server, now):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, ["imdb://tt1558972"])
    run_scan(store, connected_auth, now)
    fake_server.remove("900")
    log = run_scan(store, connected_auth, now)
    item = store.get_item_by_guid("imdb://tt1558972")
    assert log.reverted == 1 and item.status == "review" and item.status_note == "removed from Plex"
    assert item.plex_rating_key is None


def test_no_revert_when_library_fails_or_unselected_or_other_server(
    store, feed_items, connected_auth, fake_server, now
):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, ["imdb://tt1558972"])
    fake_server.add_section("6", "Other")
    run_scan(store, connected_auth, now)
    fake_server.fail_section("2")
    log = run_scan(store, connected_auth, now)
    assert log.reverted == 0 and log.ok
    assert [l["ok"] for l in log.libraries if l["key"] == "2"] == [False]
    assert store.get_item_by_guid("imdb://tt1558972").status == "library"
    fake_server.unfail("2")
    fake_server.remove("900")
    store.set_setting("selected_library_keys", ["6"])
    assert run_scan(store, connected_auth, now).reverted == 0
    store.set_setting("selected_library_keys", None)
    fake_server.machine_id = "other-server"
    assert run_scan(store, connected_auth, now).reverted == 0
    assert store.get_item_by_guid("imdb://tt1558972").status == "library"


def test_rejected_key_not_rematched(store, feed_items, connected_auth, fake_server, now):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, ["imdb://tt1558972"])
    run_scan(store, connected_auth, now)
    item = store.get_item_by_guid("imdb://tt1558972")
    apply_not_a_match(store, item.id, now)
    run_scan(store, connected_auth, now)
    assert store.get_item(item.id).status == "review"


def test_all_libraries_failing_is_not_ok(store, feed_items, connected_auth, fake_server, now):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, [])
    fake_server.fail_section("2")
    log = run_scan(store, connected_auth, now)
    assert log.ok is False and log.libraries[0]["error"]
    assert store.last_scan().ok is False


def test_identity_failure(store, connected_auth, fake_server, now):
    from app.plex_client import PlexError

    def boom():
        raise PlexError("unreachable")

    fake_server.identity = boom
    log = run_scan(store, connected_auth, now)
    assert log.ok is False and log.error == "unreachable"


def test_empty_library_reverts(store, feed_items, connected_auth, fake_server, now):
    store.upsert_feed_items(feed_items, now)
    fake_server.add_movie("2", "Movies", "900", "Kings of Pastry", 2009, ["imdb://tt1558972"])
    run_scan(store, connected_auth, now)
    fake_server.remove("900")
    log = run_scan(store, connected_auth, now)
    assert log.ok and log.reverted == 1 and log.libraries[0]["count"] == 0
