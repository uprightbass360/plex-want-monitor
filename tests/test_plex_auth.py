import json

import pytest

from app.plex_auth import PinStatus
from app.plex_client import PlexError


def test_client_id_generated_once(tmp_path, make_auth):
    a = make_auth(tmp_path / "plex.json")
    cid = a.client_id
    assert make_auth(tmp_path / "plex.json").client_id == cid
    assert oct((tmp_path / "plex.json").stat().st_mode & 0o777) == "0o600"


def test_pin_flow_claims_token(plex_auth, fake_plextv):
    pin = plex_auth.start_pin()
    assert plex_auth.poll_pin() == PinStatus("waiting", pin.code)
    fake_plextv.claim("tok-1", username="s4m")
    assert plex_auth.poll_pin().state == "claimed"
    st = plex_auth.status()
    assert st["signed_in"] and st["username"] == "s4m" and "tok" not in json.dumps(st)
    assert st["pending_pin"] is None


def test_pin_expired(plex_auth, fake_plextv, clock):
    plex_auth.start_pin()
    clock.advance(minutes=31)
    assert plex_auth.poll_pin().state == "expired"
    assert plex_auth.status()["pending_pin"] is None
    assert plex_auth.poll_pin().state == "none"


def test_poll_without_pin_is_none(plex_auth):
    assert plex_auth.poll_pin() == PinStatus("none", None)


def test_poll_transient_error_keeps_pin(plex_auth, fake_plextv):
    pin = plex_auth.start_pin()

    def boom(pin_id):
        raise PlexError("down")

    fake_plextv.check_pin = boom
    assert plex_auth.poll_pin() == PinStatus("waiting", pin.code)
    assert plex_auth.status()["pending_pin"]["code"] == pin.code


def test_state_survives_restart(signed_in_auth, make_auth, tmp_path):
    signed_in_auth.select_server("marvin-id")
    b = make_auth(tmp_path / "plex.json")
    assert b.status()["signed_in"] and b.status()["server"]["name"] == "Marvin"
    assert b.server_client() is not None


def test_select_server_picks_reachable_connection(signed_in_auth, fake_plextv, fake_http):
    fake_http.dead("https://local.marvin:32400")
    info = signed_in_auth.select_server("marvin-id")
    assert info.base_url == "https://remote.marvin:49179" and info.name == "Marvin"
    assert signed_in_auth.server_client() is not None
    st = signed_in_auth.status()
    assert st["server"]["base_url_host"] == "remote.marvin"
    assert "srv-tok" not in json.dumps(st)
    stored = json.loads(signed_in_auth.path.read_text())
    assert stored["server"]["access_token"] == "srv-tok-marvin"


def test_select_server_unreachable_stores_nothing(signed_in_auth, fake_http):
    fake_http.dead("https://local.marvin:32400")
    fake_http.dead("https://remote.marvin:49179")
    with pytest.raises(PlexError, match="no reachable connection"):
        signed_in_auth.select_server("marvin-id")
    assert signed_in_auth.status()["server"] is None


def test_select_unknown_server(signed_in_auth):
    with pytest.raises(PlexError):
        signed_in_auth.select_server("nope")


def test_list_servers_requires_token(plex_auth, signed_in_auth):
    assert [s.machine_id for s in signed_in_auth.list_servers()] == ["marvin-id", "other-server-id"]


def test_list_servers_signed_out(plex_auth):
    with pytest.raises(PlexError):
        plex_auth.list_servers()


def test_reconnect_updates_base_url(signed_in_auth, fake_http):
    signed_in_auth.select_server("marvin-id")
    fake_http.dead("https://local.marvin:32400")
    assert signed_in_auth.reconnect().base_url == "https://remote.marvin:49179"
    assert signed_in_auth.status()["server"]["base_url_host"] == "remote.marvin"


def test_reconnect_server_gone(signed_in_auth, fake_plextv):
    signed_in_auth.select_server("marvin-id")
    fake_plextv.resources = []
    with pytest.raises(PlexError):
        signed_in_auth.reconnect()


def test_signout_keeps_client_id(signed_in_auth):
    cid = signed_in_auth.client_id
    signed_in_auth.signout()
    assert signed_in_auth.client_id == cid and signed_in_auth.status()["signed_in"] is False
    assert signed_in_auth.server_client() is None


def test_concurrent_start_pin_and_signout_leave_valid_json(plex_auth):
    import threading

    errors = []

    def run(fn):
        try:
            for _ in range(60):
                fn()
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(f,)) for f in (plex_auth.start_pin, plex_auth.signout) * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    stored = json.loads(plex_auth.path.read_text())
    assert stored["client_id"] == plex_auth.client_id
    assert [p.name for p in plex_auth.path.parent.iterdir() if p.name != plex_auth.path.name and "plex" in p.name] == []


def test_server_client_timeout_passes_through(signed_in_auth):
    signed_in_auth.select_server("marvin-id")
    assert signed_in_auth.server_client(timeout=5).timeout == 5
    assert signed_in_auth.server_client().timeout == 30


def test_claim_stores_uuid_and_exposes_it(signed_in_auth, plex_auth):
    assert signed_in_auth.status()["uuid"] == "owner-uuid-0001"
    assert signed_in_auth.watchlist_client() is not None
    signed_in_auth.signout()
    assert signed_in_auth.status()["uuid"] is None and signed_in_auth.watchlist_client() is None


def test_default_watchlist_factory_builds_client(plex_auth):
    from app.plex_auth import PlexAuth
    from app.plex_watchlist import PlexWatchlist

    a = PlexAuth(plex_auth.path, plex_auth.plextv, plex_auth.server_factory, plex_auth.http, plex_auth.clock)
    assert a.watchlist_client() is None
    a._state["token"] = "t"
    assert isinstance(a.watchlist_client(), PlexWatchlist)
