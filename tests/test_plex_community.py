import json

import httpx
import pytest

from app.plex_client import PlexError
from app.plex_community import CommunityUser, PlexCommunity, resolve_authors


def make(handler):
    return PlexCommunity(httpx.Client(transport=httpx.MockTransport(handler)), "tok-secret", "cid-1")


def test_user_hit_and_token_in_header_only():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"data": {"user": {
            "id": "abc", "username": "friend-a-user", "displayName": "", "avatar": "https://api.plex.tv/a.png"}}})

    u = make(handler).user("abc")
    assert u == CommunityUser("abc", "friend-a-user", "", "https://api.plex.tv/a.png")
    req = seen["req"]
    assert req.headers["X-Plex-Token"] == "tok-secret" and req.headers["X-Plex-Client-Identifier"] == "cid-1"
    assert "tok-secret" not in str(req.url) and b"tok-secret" not in req.content
    assert json.loads(req.content)["variables"] == {"id": "abc"}
    assert str(req.url) == "https://community.plex.tv/api"


def test_user_null_returns_none():
    assert make(lambda r: httpx.Response(200, json={"data": {"user": None}})).user("x") is None


def test_http_500_and_graphql_errors_and_transport_raise_plexerror():
    with pytest.raises(PlexError) as e:
        make(lambda r: httpx.Response(500)).user("x")
    assert "tok-secret" not in str(e.value)
    with pytest.raises(PlexError):
        make(lambda r: httpx.Response(200, json={"errors": [{"message": "bad tok-secret"}]})).user("x")

    def boom(request):
        raise httpx.ConnectError("tok-secret")

    with pytest.raises(PlexError) as e:
        make(boom).user("x")
    assert "tok-secret" not in str(e.value)


def test_resolve_authors(store, seeded, fake_community, signed_in_auth, now):
    fake_community.users = {"friend-a": CommunityUser("friend-a", "ann", "Ann", "https://x/a.png")}
    assert resolve_authors(store, signed_in_auth, now) == 2
    a = {x.hash: x for x in store.list_authors()}
    assert a["friend-a"].username == "ann" and a["friend-a"].resolved_at == now
    assert a["friend-b"].username is None and a["friend-b"].resolved_at == now
    assert store.unresolved_authors() == []


def test_resolve_stops_on_error_and_skips_signed_out(store, seeded, fake_community, signed_in_auth, plex_auth, now):
    fake_community.fail = True
    assert resolve_authors(store, signed_in_auth, now) == 0
    assert len(store.unresolved_authors()) == 2
    plex_auth.signout()
    assert resolve_authors(store, plex_auth, now) == 0
