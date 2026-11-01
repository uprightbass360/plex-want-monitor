from datetime import timedelta

from app.models import FetchLog, ItemFilter
from app.poller import FetchResponse


def test_root_redirects_and_tabs_render(client, seeded):
    assert client.get("/", follow_redirects=False).status_code in (302, 307)
    html = client.get("/review").text
    assert "Battle Beyond the Stars" in html and "Severance" in html and 'href="/api/items?' in html
    for tab in ("grabbed", "library", "ignored"): assert client.get(f"/{tab}").status_code == 200
    assert client.get("/nope").status_code == 404


def test_review_hides_left_feed_unless_requested(client, seeded, store, now):
    item = store.get_item_by_guid("imdb://tt1558972"); store.mark_left_feed({i.guid for i in store.list_items(ItemFilter()) if i.id != item.id}, now)
    assert "Kings of Pastry" not in client.get("/review").text
    assert "Kings of Pastry" in client.get("/review?show_removed=1").text


def test_status_fragment_removes_row_and_updates_counts(client, seeded, store):
    item = store.get_item_by_guid("imdb://tt0080421")
    r = client.post(f"/fragments/items/{item.id}/status", data={"status": "queued", "tab": "review"})
    assert r.status_code == 200 and "Battle Beyond" not in r.text and 'hx-swap-oob' in r.text
    assert store.get_item(item.id).status == "queued"


def test_bulk_fragment(client, seeded, store):
    ids = [i.id for i in store.list_items(ItemFilter(statuses=("review",)))][:2]
    r = client.post("/fragments/items/bulk", data={"ids": ids, "status": "ignored", "tab": "review"})
    assert r.status_code == 200 and store.counts_by_status()["ignored"] == 2


def test_library_tab_shows_not_a_match_and_plex_link(client, seeded, store, now):
    item = store.get_item_by_guid("imdb://tt0080421")
    store.set_match(item.id, "1", "2", "Movies", "mach", "title", now); store.set_status(item.id, "library", now)
    html = client.get("/library").text
    assert "Not a match" in html and "app.plex.tv/desktop/#!/server/mach" in html


def test_static_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200


# ---- additional coverage ----


def test_shell_has_counts_status_and_no_secrets(client, seeded):
    html = client.get("/review").text
    assert 'id="tab-counts"' in html and 'id="header-status"' in html
    assert 'hx-trigger="every 60s"' in html and "Refresh now" in html
    assert 'hx-trigger="keyup changed delay:300ms"' in html
    assert "/static/htmx.min.js" in html and "unpkg" not in html
    assert "rss.plex.tv" not in html and "8e18-aaaa-81e5" not in html


def test_filters_apply_and_json_link_matches(client, seeded):
    html = client.get("/review?type=show").text
    assert "Severance" in html and "Battle Beyond the Stars" not in html
    assert 'href="/api/items?status=review&amp;in_feed=true&amp;type=show' in html
    html = client.get("/review?q=sever").text
    assert "Severance" in html and "Battle Beyond the Stars" not in html


def test_rows_fragment_and_empty_state(client, seeded):
    r = client.get("/fragments/review/rows", params={"type": "movie"})
    assert r.status_code == 200 and "Battle Beyond the Stars" in r.text and "Severance" not in r.text
    assert "<html" not in r.text
    assert "Nothing to review" in client.get("/fragments/review/rows", params={"q": "zzz"}).text
    assert client.get("/fragments/nope/rows").status_code == 404
    assert client.get("/fragments/review/rows", params={"type": "bogus"}).status_code == 422


def test_title_is_escaped(client, seeded, store):
    item = store.get_item_by_guid("imdb://tt0080421")
    store.conn.execute("UPDATE items SET title=? WHERE id=?", ("<script>x</script>", item.id))
    html = client.get("/review").text
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html


def test_status_fragment_errors(client, seeded, store, now):
    item = store.get_item_by_guid("imdb://tt0080421")
    r = client.post(f"/fragments/items/{item.id}/status", data={"status": "grabbed", "tab": "review"})
    assert r.status_code == 409 and store.get_item(item.id).status == "review"
    r = client.post("/fragments/items/99999/status", data={"status": "queued", "tab": "review"})
    assert r.status_code == 404


def test_status_fragment_keeps_row_when_still_in_tab(client, seeded, store, now):
    item = store.get_item_by_guid("imdb://tt0080421")
    store.set_status(item.id, "ignored", now)
    # Unignore from the ignored tab: the row leaves; viewed from review it stays.
    r = client.post(f"/fragments/items/{item.id}/status", data={"status": "review", "tab": "review"})
    assert r.status_code == 200 and "Battle Beyond the Stars" in r.text and 'id="tab-counts"' in r.text


def test_grabbed_and_ignored_row_actions(client, seeded, store, now):
    a = store.get_item_by_guid("imdb://tt0080421")
    b = store.get_item_by_guid("imdb://tt1558972")
    store.set_status(a.id, "queued", now); store.set_status(a.id, "grabbed", now)
    store.set_status(b.id, "ignored", now)
    grabbed = client.get("/grabbed").text
    assert "Battle Beyond the Stars" in grabbed and "Undo" in grabbed and "Kings of Pastry" not in grabbed
    ignored = client.get("/ignored").text
    assert "Kings of Pastry" in ignored and "Unignore" in ignored


def test_not_a_match_fragment(client, seeded, store, now):
    item = store.get_item_by_guid("imdb://tt0080421")
    store.set_match(item.id, "1", "2", "Movies", "mach", "title", now); store.set_status(item.id, "library", now)
    r = client.post(f"/fragments/items/{item.id}/not-a-match", data={"tab": "library"})
    assert r.status_code == 200 and "Battle Beyond" not in r.text and "hx-swap-oob" in r.text
    assert store.get_item(item.id).status == "review"
    assert client.post(f"/fragments/items/{item.id}/not-a-match", data={"tab": "library"}).status_code == 409
    assert client.post("/fragments/items/99999/not-a-match", data={"tab": "library"}).status_code == 404


def test_bulk_fragment_rerenders_with_filters(client, seeded, store):
    ids = [i.id for i in store.list_items(ItemFilter(statuses=("review",), type="movie"))][:1]
    r = client.post(
        "/fragments/items/bulk", data={"ids": ids, "status": "queued", "tab": "review", "type": "show"}
    )
    assert r.status_code == 200 and "Severance" in r.text and "Battle Beyond" not in r.text
    assert 'id="tab-counts"' in r.text


def test_header_status_shows_ages_truncation_and_error(client, store, now, monkeypatch):
    monkeypatch.setattr("app.web.pages._now", lambda: now)
    start = now - timedelta(minutes=4)
    store.log_fetch(FetchLog(start, start, False, error="boom 500", truncated=True))
    html = client.get("/fragments/header-status").text
    assert "boom 500" in html and "truncated" in html.lower()
    assert "feed 4 min ago" in html and "scan never" in html


def test_ago_filter(now):
    from app.web.pages import ago

    assert ago(None, now) == "never"
    assert ago(now - timedelta(seconds=30), now) == "just now"
    assert ago(now - timedelta(minutes=12), now) == "12 min ago"
    assert ago(now - timedelta(hours=5), now) == "5 h ago"
    assert ago(now - timedelta(days=3), now) == "3 days ago"


def test_refresh_runs_fetch_and_scan(client, store, fetcher, fixture_bytes):
    store.set_setting("feed_url", "https://rss.plex.tv/8e18-aaaa-81e5")
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    r = client.post("/fragments/refresh")
    assert r.status_code == 200 and 'id="header-status"' in r.text
    assert store.last_fetch() is not None and store.last_scan() is not None
    assert "8e18-aaaa-81e5" not in r.text


# ---- queue tab ----


def _queue_two(store, now):
    ids = [i.id for i in store.list_items(ItemFilter(statuses=("review",)))][:2]
    for i in ids: store.set_status(i, "queued", now)
    return ids


def test_queue_page_and_got_it_keeps_faded_row(client, seeded, store, now):
    ids = [i.id for i in store.list_items(ItemFilter(statuses=("review",)))][:2]
    for i in ids: store.set_status(i, "queued", now)
    html = client.get("/queue").text
    assert "Copy all as text" in html and "Got it" in html and 'data-copy="Battle Beyond the Stars 1980"' in html
    r = client.post(f"/fragments/items/{ids[0]}/status", data={"status": "grabbed", "tab": "queue"})
    assert 'class="qrow done"' in r.text and "Undo" in r.text
    r = client.post(f"/fragments/items/{ids[0]}/status", data={"status": "queued", "tab": "queue"})
    assert "done" not in r.text and "Got it" in r.text


def test_skip_reorders(client, seeded, store, now):
    ids = [i.id for i in store.list_items(ItemFilter(statuses=("review",)))][:2]
    for i in ids: store.set_status(i, "queued", now)
    r = client.post(f"/fragments/items/{ids[0]}/skip", data={"tab": "queue"})
    assert r.text.index(f'data-item-id="{ids[1]}"') < r.text.index(f'data-item-id="{ids[0]}"')


def test_queue_page_header_json_and_no_secrets(client, seeded, store, now):
    _queue_two(store, now)
    html = client.get("/queue").text
    assert 'href="/api/items?status=queued&amp;sort=queue"' in html
    assert "oldest first" in html and 'id="queue-progress"' in html and 'data-total="2"' in html
    assert "Put back" in html and "Ignore" in html and "rss.plex.tv" not in html
    assert 'href="/queue"' in html and 'id="tab-counts"' in html


def test_queue_filter_fragment_and_put_back(client, seeded, store, now):
    ids = _queue_two(store, now)
    r = client.get("/fragments/queue/rows?type=show")
    assert "Battle Beyond the Stars" not in r.text
    r = client.post(f"/fragments/items/{ids[0]}/status", data={"status": "review", "tab": "queue"})
    assert f'data-item-id="{ids[0]}"' not in r.text and store.get_item(ids[0]).status == "review"


# ---- settings page ----


def test_settings_renders_and_saves_feed(client, store):
    assert "Connect to Plex" in client.get("/settings").text
    r = client.post("/fragments/settings/feed", data={"feed_url": "https://rss.plex.tv/zzzz-81e5", "feed_interval_min": "10", "feed_cap_threshold": "25"})
    assert "Saved" in r.text and "zzzz" not in r.text and "81e5" in r.text
    assert store.get_setting("feed_interval_min") == 10
    r = client.post("/fragments/settings/feed", data={"feed_url": "", "feed_interval_min": "10", "feed_cap_threshold": "25"})
    assert store.get_setting("feed_url").endswith("81e5")


def test_plex_pin_card_flow(client, fake_plextv):
    r = client.post("/fragments/plex/pin"); assert "plex.tv/link" in r.text and 'hx-trigger="every 3s"' in r.text
    fake_plextv.claim("tok", username="s4m")
    r = client.get("/fragments/plex/pin"); assert "s4m" in r.text and "tok" not in r.text
    r = client.post("/fragments/plex/server", data={"machine_id": "marvin-id"})
    assert "Marvin" in r.text and "Sign out" in r.text and 'name="library_keys"' in r.text


def test_friends_label(client, seeded, store):
    r = client.post("/fragments/settings/friends", data={"hash": "friend-a", "label": "Sam"})
    assert "Sam" in r.text and {a.hash: a.label for a in store.list_authors()}["friend-a"] == "Sam"


def test_settings_page_has_tab_cards_and_no_secrets(connected_client, seeded, fake_server):
    fake_server.add_section("2", "Movies"); fake_server.add_section("3", "TV Shows")
    html = connected_client.get("/settings").text
    assert 'href="/settings"' in html and 'id="feed-card"' in html and 'id="plex-card"' in html
    assert 'id="friends-card"' in html and "/api/items/queue.txt" in html and "/api/status" in html
    assert "8e18" not in html and "81e5" in html and "tok-1" not in html and "srv-tok" not in html
    assert "Marvin" in html and "https://local.marvin" not in html
    assert 'hx-confirm' in html and 'name="scan_interval_min"' in html
    assert html.count('name="library_keys"') == 2 and html.count(" checked") >= 2


def test_feed_validation_error_is_422_and_not_saved(client, store):
    r = client.post("/fragments/settings/feed", data={"feed_url": "", "feed_interval_min": "0", "feed_cap_threshold": "25"})
    assert r.status_code == 422 and 'id="feed-card"' in r.text and "between 1 and 1440" in r.text
    r = client.post("/fragments/settings/feed", data={"feed_url": "", "feed_interval_min": "5", "feed_cap_threshold": "501"})
    assert r.status_code == 422 and store.get_setting("feed_interval_min") is None
    r = client.post("/fragments/settings/feed", data={"feed_url": "", "feed_interval_min": "abc", "feed_cap_threshold": "25"})
    assert r.status_code == 422


def test_feed_test_does_not_save_or_leak(client, store, fetcher, fixture_bytes):
    fetcher.queue(FetchResponse(200, fixture_bytes, "etag-x"))
    r = client.post("/fragments/settings/feed/test", data={"feed_url": "https://rss.plex.tv/secret-7777", "feed_interval_min": "10", "feed_cap_threshold": "25"})
    assert " items" in r.text and "secret" not in r.text
    assert fetcher.last_url.endswith("secret-7777") and fetcher.last_etag is None
    assert store.get_setting("feed_url") is None and store.get_setting("feed_etag") is None
    r = client.post("/fragments/settings/feed/test", data={"feed_url": "https://rss.plex.tv/secret-7777"})
    assert "AssertionError" in r.text and "secret" not in r.text


def test_fetch_now_rerenders_feed_card(client, seeded, store, fetcher, fixture_bytes):
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    r = client.post("/fragments/settings/feed/fetch")
    assert 'id="feed-card"' in r.text and "Last fetch" in r.text and "8e18" not in r.text


def test_plex_expired_and_none_states(client, fake_plextv, clock):
    client.post("/fragments/plex/pin")
    clock.advance(minutes=31)
    r = client.get("/fragments/plex/pin")
    assert "Start again" in r.text and "every 3s" not in r.text
    r = client.get("/fragments/plex/pin")
    assert "Connect to Plex" in r.text and "every 3s" not in r.text


def test_single_server_auto_selects(client, fake_plextv):
    del fake_plextv.resources[1]
    client.post("/fragments/plex/pin"); fake_plextv.claim("tok")
    r = client.get("/fragments/plex/pin")
    assert "Sign out" in r.text and "Marvin" in r.text and "1.43.5" in r.text


def test_plex_server_error_is_502(client, fake_plextv):
    client.post("/fragments/plex/pin"); fake_plextv.claim("tok"); client.get("/fragments/plex/pin")
    r = client.post("/fragments/plex/server", data={"machine_id": "nope"})
    assert r.status_code == 502 and 'id="plex-card"' in r.text and "server not found" in r.text


def test_libraries_scan_interval_reconnect_signout(connected_client, store):
    c = connected_client
    r = c.post("/fragments/settings/libraries", data={"library_keys": ["2"]})
    assert store.get_setting("selected_library_keys") == ["2"] and "Saved" in r.text
    r = c.post("/fragments/settings/libraries", data={})
    assert store.get_setting("selected_library_keys") is None
    r = c.post("/fragments/settings/scan", data={"scan_interval_min": "45"})
    assert store.get_setting("scan_interval_min") == 45 and 'value="45"' in r.text
    r = c.post("/fragments/settings/scan", data={"scan_interval_min": "5000"})
    assert r.status_code == 422 and 'id="plex-card"' in r.text
    assert "Sign out" in c.post("/fragments/plex/reconnect").text
    r = c.post("/fragments/plex/signout")
    assert "Connect to Plex" in r.text and "Sign out" not in r.text


def test_scan_now_shows_summary(connected_client, store):
    r = connected_client.post("/fragments/settings/scan/run")
    assert 'id="plex-card"' in r.text and "Last scan" in r.text


def test_friends_row_escapes_label(client, seeded):
    r = client.post("/fragments/settings/friends", data={"hash": "friend-a", "label": "<b>x</b>"})
    assert "<b>x</b>" not in r.text and "&lt;b&gt;" in r.text


def test_settings_error_cards_carry_swap_header_and_base_config_unchanged(client, connected_client):
    r = client.post("/fragments/settings/feed", data={"feed_interval_min": "0", "feed_cap_threshold": "25"})
    assert r.status_code == 422 and r.headers["X-WM-Card"] == "1"
    r = connected_client.post("/fragments/settings/scan", data={"scan_interval_min": "0"})
    assert r.status_code == 422 and r.headers["X-WM-Card"] == "1"
    assert "X-WM-Card" not in client.post("/fragments/settings/feed", data={"feed_interval_min": "5", "feed_cap_threshold": "25"}).headers
    assert "422" not in client.get("/review").text.split("htmx-config")[1].split(">")[0]
