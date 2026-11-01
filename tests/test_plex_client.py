import logging

import httpx
import pytest

from app.plex_client import PlexError, PlexServerClient, PlexTV, pick_connection


@pytest.fixture
def plextv(transport):
    return PlexTV("cid-1", transport.client)


@pytest.fixture
def server_client(transport):
    return PlexServerClient("https://192-168-0-86.plex.direct:32400", "secret-tok", transport.client)


@pytest.fixture
def server_client_broken(transport):
    transport.broken = True
    return PlexServerClient("https://192-168-0-86.plex.direct:32400", "secret-tok", transport.client)


@pytest.fixture
def resource(plextv):
    return plextv.servers("tok")[0]


def test_create_and_check_pin(plextv, transport):
    pin = plextv.create_pin()
    assert pin.id == 415990614 and pin.code == "ABCD" and pin.expires_at.tzinfo is not None
    assert transport.last_request.headers["X-Plex-Client-Identifier"] == "cid-1"
    assert plextv.check_pin(pin.id) is None
    transport.claim_pin("tok-123")
    assert plextv.check_pin(pin.id) == "tok-123"


def test_user(plextv):
    u = plextv.user("tok")
    assert (u.id, u.username) == (1, "tester")


def test_servers_filters_to_servers_and_maps_fields(plextv):
    servers = plextv.servers("tok")
    assert [s.name for s in servers] == ["Marvin", "other-server"]
    assert servers[0].owned and servers[0].connections[0].local is True
    assert servers[0].machine_id.startswith("6b77") and servers[0].version == "1.43.5.11079"
    assert servers[0].access_token == "srv-tok-marvin"
    assert not servers[1].owned


def test_server_client_parses(server_client):
    assert server_client.identity().machine_id.startswith("6b77")
    assert [(l.key, l.type) for l in server_client.sections()][:2] == [("7", "movie"), ("2", "movie")]
    entries = server_client.list_all("2")
    assert entries[0].guids == ("imdb://tt0096734", "tmdb://11974", "tvdb://5869")
    assert any(e.guids == () and e.year is None for e in entries)
    assert entries[0].type == "movie" and entries[0].rating_key == "14696"


def test_token_is_header_not_query_and_only_get(server_client, transport):
    server_client.list_all("3")
    req = transport.last_request
    assert req.method == "GET"
    assert req.headers["X-Plex-Token"] == "secret-tok"
    assert "secret-tok" not in str(req.url)
    assert req.url.params["includeGuids"] == "1"


def test_server_errors_wrapped(server_client_broken):
    with pytest.raises(PlexError) as ei:
        server_client_broken.sections()
    msg = str(ei.value)
    assert "500" in msg and "plex.direct" in msg and "secret-tok" not in msg


def test_connection_failure_wrapped_without_token(transport):
    transport.dead("https://192-168-0-86.plex.direct:32400")
    c = PlexServerClient("https://192-168-0-86.plex.direct:32400", "secret-tok", transport.client)
    with pytest.raises(PlexError) as ei:
        c.identity()
    assert "secret-tok" not in str(ei.value)


def test_non_utf8_bytes_replaced():
    body = b'{"MediaContainer":{"Metadata":[{"ratingKey":"1","title":"Caf\xe9","type":"movie"}]}}'
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    entries = PlexServerClient("https://x:1", "t", http).list_all("2")
    assert entries[0].title == "Caf�"


def test_plextv_error_wrapped(transport):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(PlexError) as ei:
        PlexTV("cid", http).user("secret-tok")
    assert "401" in str(ei.value) and "plex.tv" in str(ei.value)


def test_pick_connection_prefers_local_that_answers(resource, transport):
    transport.dead("https://192-168-0-86.plex.direct:32400")
    assert pick_connection(resource, "tok", transport.client) == "https://remote.example:49179"


def test_pick_connection_local_first_and_skips_relay(resource, transport):
    from dataclasses import replace
    from app.plex_client import Connection

    assert pick_connection(resource, "tok", transport.client) == "https://192-168-0-86.plex.direct:32400"
    relay_only = replace(resource, connections=(Connection("https://relay.example", False, True),))
    assert pick_connection(relay_only, "tok", transport.client) is None
    assert all("relay" not in str(r.url) for r in transport.requests[-1:])


def test_pick_connection_none_when_all_dead(resource, transport):
    transport.dead("https://192-168-0-86.plex.direct:32400")
    transport.dead("https://remote.example:49179")
    assert pick_connection(resource, "tok", transport.client) is None


def test_no_token_in_logs(server_client_broken, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(PlexError):
        server_client_broken.sections()
    assert "secret-tok" not in caplog.text


def _paged_http(total, pages_seen, include_total=True, fail_start=None):
    def handler(r):
        start = int(r.url.params["X-Plex-Container-Start"])
        size = int(r.url.params["X-Plex-Container-Size"])
        pages_seen.append((start, size))
        if start == fail_start:
            return httpx.Response(500)
        items = [
            {"ratingKey": str(i), "title": f"T{i}", "type": "movie"}
            for i in range(start, min(start + size, total))
        ]
        mc = {"Metadata": items}
        if include_total:
            mc["totalSize"] = total
        return httpx.Response(200, json={"MediaContainer": mc})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_all_concatenates_pages_in_order(transport):
    c = PlexServerClient("https://x:1", "t", transport.client)
    entries = c.list_all("2", page_size=2)
    assert len(entries) == 3 and entries[0].rating_key == "14696"
    reqs = [r for r in transport.requests if r.url.path.endswith("/all")]
    assert [r.url.params["X-Plex-Container-Start"] for r in reqs] == ["0", "2"]
    assert all(r.url.params["X-Plex-Container-Size"] == "2" for r in reqs)
    assert all(r.url.params["includeGuids"] == "1" for r in reqs)


def test_list_all_honours_total_size_and_stops():
    seen = []
    c = PlexServerClient("https://x:1", "t", _paged_http(5, seen))
    assert [e.rating_key for e in c.list_all("2", page_size=2)] == ["0", "1", "2", "3", "4"]
    assert seen == [(0, 2), (2, 2), (4, 2)]


def test_list_all_without_total_size_short_page_is_last():
    seen = []
    c = PlexServerClient("https://x:1", "t", _paged_http(5, seen, include_total=False))
    assert len(c.list_all("2", page_size=2)) == 5
    assert seen == [(0, 2), (2, 2), (4, 2)]


def test_list_all_default_page_size_200():
    seen = []
    PlexServerClient("https://x:1", "t", _paged_http(1, seen)).list_all("2")
    assert seen == [(0, 200)]


def test_list_all_error_on_later_page_raises_plex_error():
    c = PlexServerClient("https://x:1", "secret-tok", _paged_http(5, [], fail_start=2))
    with pytest.raises(PlexError) as ei:
        c.list_all("2", page_size=2)
    assert "500" in str(ei.value) and "secret-tok" not in str(ei.value)


def test_list_all_runaway_server_bounded():
    def handler(r):
        return httpx.Response(
            200, json={"MediaContainer": {"totalSize": 10, "Metadata": [{"ratingKey": "1", "title": "x", "type": "movie"}]}}
        )

    c = PlexServerClient("https://x:1", "t", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PlexError):
        c.list_all("2", page_size=2)
