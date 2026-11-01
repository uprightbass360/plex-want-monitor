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
