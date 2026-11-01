import json

from app.config import Config
from app.poller import FetchResponse
from app.web.app import create_app


def test_healthz_and_status(client):
    assert client.get("/healthz").json() == {"ok": True}
    body = client.get("/api/status").json()
    assert set(body) == {"counts", "last_fetch", "last_scan", "plex"}
    assert "token" not in json.dumps(body).lower()


def test_fetch_and_scan_now(connected_client, fetcher, fixture_bytes, store, fake_server):
    fake_server.add_section("2", "Movies")
    store.set_setting("feed_url", "u")
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    assert connected_client.post("/api/feed/fetch").json()["new_count"] == 5
    assert connected_client.post("/api/plex/scan").json()["ok"] is True
    body = connected_client.get("/api/status").json()
    assert body["last_fetch"]["new_count"] == 5 and body["last_scan"]["ok"] is True


def test_scan_when_signed_out_reports_not_connected(client):
    assert client.post("/api/plex/scan").json()["error"] == "Plex not connected"


def test_state_exposed(client, store, plex_auth, fetcher, config):
    st = client.app.state
    assert st.store is store and st.auth is plex_auth and st.fetcher is fetcher
    assert st.config is config and st.scheduler is not None


def test_builds_defaults_and_creates_data_dir(tmp_path):
    cfg = Config(data_dir=tmp_path / "nested" / "data", port=8787, log_level="INFO")
    app = create_app(cfg, scheduler_enabled=False)
    assert cfg.db_path.exists()
    assert app.state.store.counts_by_status() is not None
