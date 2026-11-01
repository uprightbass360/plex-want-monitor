def _ids(client, **params):
    return [i["id"] for i in client.get("/api/items", params=params).json()["items"]]


def test_list_defaults_to_review_in_feed(client, seeded):
    body = client.get("/api/items").json()
    assert body["count"] == 5 and all(i["status"] == "review" for i in body["items"])


def test_list_validation(client, seeded):
    assert client.get("/api/items", params={"type": "bogus"}).status_code == 422
    assert client.get("/api/items", params={"sort": "bogus"}).status_code == 422
    assert client.get("/api/items", params={"status": "review,bogus"}).status_code == 422
    assert client.get("/api/items", params={"status": "review,queued"}).status_code == 200


def test_status_change_and_invalid(client, seeded):
    item = client.get("/api/items").json()["items"][0]
    assert client.post(f"/api/items/{item['id']}/status", json={"status": "queued"}).json()["status"] == "queued"
    r = client.post(f"/api/items/{item['id']}/status", json={"status": "grabbed"})
    assert r.status_code == 200
    r = client.post(f"/api/items/{item['id']}/status", json={"status": "ignored"})
    assert r.status_code == 409 and "error" in r.json()
    assert client.post(f"/api/items/{item['id']}/status", json={"status": "bogus"}).status_code == 409
    assert client.post("/api/items/99999/status", json={"status": "queued"}).status_code == 404


def test_illegal_transition_leaves_row_unchanged(client, seeded, store, now):
    iid = _ids(client)[0]
    store.set_status(iid, "library", now)
    r = client.post(f"/api/items/{iid}/status", json={"status": "queued"})
    assert r.status_code == 409
    assert store.get_item(iid).status == "library"
    r = client.post(f"/api/items/{iid}/status", json={"status": "bogus"})
    assert r.status_code == 409 and store.get_item(iid).status == "library"


def test_bulk_partial_success(client, seeded):
    ids = _ids(client)
    client.post(f"/api/items/{ids[0]}/status", json={"status": "ignored"})
    r = client.post("/api/items/bulk", json={"ids": ids[:3], "status": "queued"}).json()
    assert len(r["updated"]) == 2 and r["skipped"][0]["id"] == ids[0]
    r = client.post("/api/items/bulk", json={"ids": [99999], "status": "queued"}).json()
    assert r["updated"] == [] and r["skipped"][0]["id"] == 99999 and "error" in r["skipped"][0]
    assert client.post("/api/items/bulk", json={"ids": "x"}).status_code == 422


def test_queue_txt_and_skip(client, seeded):
    ids = _ids(client)[:2]
    for i in ids:
        client.post(f"/api/items/{i}/status", json={"status": "queued"})
    assert client.post(f"/api/items/{ids[0]}/skip").status_code == 200
    r = client.get("/api/items/queue.txt")
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert len(lines) == 2 and lines[-1].startswith("Battle Beyond the Stars")
    assert client.post("/api/items/99999/skip").status_code == 404


def test_not_a_match(client, seeded, store, now):
    iid = _ids(client)[0]
    assert client.post(f"/api/items/{iid}/not-a-match").status_code == 409
    store.set_status(iid, "library", now)
    r = client.post(f"/api/items/{iid}/not-a-match")
    assert r.status_code == 200 and r.json()["status"] == "review"
    assert client.post("/api/items/99999/not-a-match").status_code == 404


def test_settings_masks_url_and_validates(client):
    client.put("/api/settings", json={"feed_url": "https://rss.plex.tv/8e18-aaaa-81e5", "feed_interval_min": 5})
    body = client.get("/api/settings").json()
    assert body["feed_url_masked"].endswith("81e5") and "8e18" not in body["feed_url_masked"]
    assert "8e18" not in client.get("/api/settings").text
    assert body["feed_interval_min"] == 5
    for k, v in [("feed_interval_min", 0), ("scan_interval_min", 1441), ("feed_cap_threshold", 501)]:
        assert client.put("/api/settings", json={k: v}).status_code == 422


def test_settings_friends_and_empty_url(client, seeded, store):
    h = store.list_authors()[0].hash
    body = client.put("/api/settings", json={"feed_url": "", "friends": [{"hash": h, "label": "Pat"}],
                                              "selected_library_keys": ["2"]}).json()
    assert store.get_setting("feed_url").endswith("81e5")
    assert body["selected_library_keys"] == ["2"]
    assert next(f for f in body["friends"] if f["hash"] == h)["label"] == "Pat"


def test_plex_pin_endpoints(client, fake_plextv):
    code = client.post("/api/plex/pin").json()["code"]
    assert client.get("/api/plex/pin").json() == {"state": "waiting", "code": code}
    fake_plextv.claim("tok", username="u")
    assert client.get("/api/plex/pin").json()["state"] == "claimed"
    assert client.get("/api/plex/servers").status_code == 200


def test_plex_signed_out(client):
    r = client.get("/api/plex/libraries")
    assert r.status_code == 409 and r.json() == {"error": "not signed in"}
    assert client.get("/api/plex/servers").status_code == 409


def test_plex_server_select_libraries_signout(client, fake_plextv, fake_server=None):
    client.post("/api/plex/pin")
    fake_plextv.claim("tok", username="u")
    client.get("/api/plex/pin")
    servers = client.get("/api/plex/servers").json()
    assert {"name", "machine_id", "owned", "version"} == set(servers[0])
    assert client.post("/api/plex/server", json={"machine_id": "nope"}).status_code == 502
    assert client.post("/api/plex/server", json={"machine_id": "marvin-id"}).json()["signed_in"] is True
    assert client.post("/api/plex/reconnect").status_code == 200
    assert client.post("/api/plex/signout").json()["signed_in"] is False


def test_plex_libraries(connected_client, fake_server):
    fake_server.add_section("2", "Movies")
    assert connected_client.get("/api/plex/libraries").json() == [
        {"key": "2", "type": "movie", "title": "Movies"}
    ]


def test_settings_unset_defaults(client):
    body = client.get("/api/settings").json()
    assert body["feed_url_masked"] is None
    assert (body["feed_interval_min"], body["scan_interval_min"], body["feed_cap_threshold"]) == (15, 30, 25)


def test_friends_resolve_api(client, signed_in_auth, seeded, fake_community):
    from app.plex_community import CommunityUser

    fake_community.users = {
        "friend-a": CommunityUser("friend-a", "ann", "Ann", "https://x/a.png"),
        "friend-b": CommunityUser("friend-b", "bob", "", None),
    }
    assert client.post("/api/friends/resolve").json() == {"resolved": 2}
    f = {x["hash"]: x for x in client.get("/api/settings").json()["friends"]}
    assert f["friend-a"]["username"] == "ann" and f["friend-a"]["name"] == "Ann"
    assert f["friend-b"]["name"] == "bob" and f["friend-b"]["avatar_url"] is None
    assert set(f["friend-a"]) == {"hash", "label", "username", "display_name", "avatar_url", "name", "item_count"}


def test_friends_resolve_signed_out_409(client, seeded):
    r = client.post("/api/friends/resolve")
    assert r.status_code == 409 and r.json() == {"error": "not signed in"}
