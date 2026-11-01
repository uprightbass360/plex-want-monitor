"""Settings page and its htmx fragments (feed, Plex sign-in, libraries, friends)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.feed import parse_feed
from app.plex_client import PlexError
from app.web.settings_shared import DEFAULTS, parse_setting_int, range_error, select_server as _select_server
from app.web.pages import TABS, _counts, _header_status, _now, templates

router = APIRouter()

Text = Annotated[str | None, Form()]
CARD_HEADER = {"X-WM-Card": "1"}  # tells app.js to swap this 422/502 card response


def _card(request: Request, name: str, ctx: dict, code: int = 200) -> HTMLResponse:
    response = templates.TemplateResponse(request, f"partials/{name}.html", ctx, status_code=code)
    if code >= 400:
        response.headers.update(CARD_HEADER)
    return response


# ---- feed card ----


def _feed_ctx(request: Request, **extra) -> dict:
    store = request.app.state.store
    url = store.get_setting("feed_url")
    return {
        "masked": "…" + url[-4:] if url else None,
        "interval": store.get_setting("feed_interval_min", DEFAULTS["feed_interval_min"]),
        "cap": store.get_setting("feed_cap_threshold", DEFAULTS["feed_cap_threshold"]),
        "fetch": store.last_fetch(),
        "watchlist_enabled": store.get_setting("own_watchlist_enabled", DEFAULTS["own_watchlist_enabled"]),
        "watchlist": store.last_fetch(source="watchlist"),
        "now": _now(),
        "note": None, "error": None, "test": None,
        **extra,
    }


@router.get("/fragments/settings/feed", response_class=HTMLResponse)
def feed_card(request: Request):
    return _card(request, "feed_card", _feed_ctx(request))


@router.post("/fragments/settings/feed", response_class=HTMLResponse)
def save_feed(request: Request, feed_url: Text = None, feed_interval_min: Text = None,
              feed_cap_threshold: Text = None, own_watchlist_enabled: Text = None):
    store = request.app.state.store
    interval = parse_setting_int("feed_interval_min", feed_interval_min)
    cap = parse_setting_int("feed_cap_threshold", feed_cap_threshold)
    if interval is None or cap is None:
        error = range_error("Poll interval", "feed_interval_min") if interval is None else range_error("Cap threshold", "feed_cap_threshold")
        shown = {"interval": feed_interval_min, "cap": feed_cap_threshold}
        return _card(request, "feed_card", _feed_ctx(request, error=error, **shown), 422)
    url = (feed_url or "").strip()
    if url:
        store.set_setting("feed_url", url)
    store.set_setting("feed_interval_min", interval)
    store.set_setting("feed_cap_threshold", cap)
    store.set_setting("own_watchlist_enabled", own_watchlist_enabled is not None)
    return _card(request, "feed_card", _feed_ctx(request, note="Saved"))


@router.post("/fragments/settings/feed/test", response_class=HTMLResponse)
def test_feed(request: Request, feed_url: Text = None):
    """Fetch the form's URL (or the stored one) without saving anything."""
    store = request.app.state.store
    url = (feed_url or "").strip() or store.get_setting("feed_url")
    if not url:
        result = {"ok": False, "text": "No feed URL to test."}
    else:
        try:
            resp = request.app.state.fetcher.fetch(url, None)
            if not 200 <= resp.status_code < 300:
                result = {"ok": False, "text": f"HTTP {resp.status_code}"}
            else:
                result = {"ok": True, "text": f"{len(parse_feed(resp.body or b'').items)} items"}
        except Exception as exc:  # class name only: the message may contain the URL
            result = {"ok": False, "text": type(exc).__name__}
    return _card(request, "feed_card", _feed_ctx(request, test=result))


@router.post("/fragments/settings/feed/fetch", response_class=HTMLResponse)
async def fetch_feed(request: Request):
    result = await request.app.state.scheduler.fetch_now()
    note = None if result else "A fetch is already running."
    return _card(request, "feed_card", _feed_ctx(request, note=note))


@router.post("/fragments/settings/watchlist/sync", response_class=HTMLResponse)
async def sync_watchlist(request: Request):
    if request.app.state.auth.watchlist_client() is None:
        return _card(request, "feed_card", _feed_ctx(request, error="Sign in to Plex to sync your watchlist."), 409)
    result = await request.app.state.scheduler.watchlist_now()
    if result is None:
        note = "A fetch is already running."
    else:
        note = result.error.capitalize() + "." if result.id is None and not result.ok else None
    return _card(request, "feed_card", _feed_ctx(request, note=note))


# ---- plex card ----


LIVE_SECTIONS_TIMEOUT = 5.0  # only used when there is no scan log to read libraries from


def _libraries(store, auth, server: dict) -> list[dict]:
    """Library checkboxes: from the last scan on this server when there is one, else one short live call."""
    selected = store.get_setting("selected_library_keys") or None
    scan = store.last_scan()
    if scan and scan.libraries and scan.machine_id == server["machine_id"]:
        libs = [(lib["key"], lib.get("name") or lib["key"]) for lib in scan.libraries]
    else:
        client = auth.server_client(timeout=LIVE_SECTIONS_TIMEOUT)
        libs = [(s.key, s.title) for s in (client.sections() if client else [])]
    return [{"key": k, "title": t, "checked": selected is None or k in selected} for k, t in libs]


def _plex_ctx(request: Request, *, expired: bool = False, note: str | None = None,
              error: str | None = None, scan_interval=None, library_error: str | None = None) -> dict:
    store, auth = request.app.state.store, request.app.state.auth
    ctx = {"expired": expired, "note": note, "error": error, "now": _now(), "servers": [],
           "libraries": [], "scan": None, "scan_interval": scan_interval,
           "library_error": library_error, "server_error": None}
    status = auth.status()
    if status["signed_in"] and not status["server"]:  # "pick" state: the only automatic plex.tv call
        try:
            ctx["servers"] = auth.list_servers()
            if len(ctx["servers"]) == 1:
                _select_server(store, auth, ctx["servers"][0].machine_id)
                status = auth.status()
                ctx["servers"] = []
        except PlexError as exc:
            ctx["error"] = str(exc)
    if status["signed_in"] and status["server"]:
        try:
            ctx["libraries"] = _libraries(store, auth, status["server"])
        except PlexError as exc:
            ctx["error"] = ctx["error"] or str(exc)
        ctx["scan"] = store.last_scan()
        if ctx["scan_interval"] is None:
            ctx["scan_interval"] = store.get_setting("scan_interval_min", DEFAULTS["scan_interval_min"])
    state = ("connected" if status["server"] else "pick") if status["signed_in"] else (
        "pending" if status["pending_pin"] else "expired" if expired else "connect")
    return {**ctx, "plex": status, "state": state}


def _plex(request: Request, code: int = 200, **kw) -> HTMLResponse:
    return _card(request, "plex_card", _plex_ctx(request, **kw), code)


@router.get("/fragments/settings/plex", response_class=HTMLResponse)
def plex_card(request: Request):
    return _plex(request)


@router.post("/fragments/plex/pin", response_class=HTMLResponse)
def start_pin(request: Request):
    try:
        request.app.state.auth.start_pin()
    except PlexError as exc:
        return _plex(request, 502, error=str(exc))
    return _plex(request)


@router.get("/fragments/plex/pin", response_class=HTMLResponse)
def poll_pin(request: Request):
    pin = request.app.state.auth.poll_pin()
    return _plex(request, expired=pin.state == "expired")


@router.get("/fragments/plex/servers", response_class=HTMLResponse)
def server_picker(request: Request):
    """The server dropdown, populated from plex.tv on demand ("Change server…")."""
    auth = request.app.state.auth
    status = auth.status()
    ctx = {"plex": status, "servers": [], "server_error": None}
    code = 200
    try:
        ctx["servers"] = auth.list_servers()
    except PlexError as exc:
        ctx["server_error"], code = str(exc), 502
    if status["server"] is None:  # nothing to pick against: show the whole card instead
        return _plex(request, code, error=ctx["server_error"])
    return _card(request, "server_picker", ctx, code)


@router.post("/fragments/plex/server", response_class=HTMLResponse)
def select_server(request: Request, machine_id: Annotated[str, Form()]):
    try:
        _select_server(request.app.state.store, request.app.state.auth, machine_id)
    except PlexError as exc:
        return _plex(request, 502, error=str(exc))
    return _plex(request)


@router.post("/fragments/plex/reconnect", response_class=HTMLResponse)
def reconnect(request: Request):
    try:
        request.app.state.auth.reconnect()
    except PlexError as exc:
        return _plex(request, 502, error=str(exc))
    return _plex(request, note="Reconnected")


@router.post("/fragments/plex/signout", response_class=HTMLResponse)
def signout(request: Request):
    request.app.state.auth.signout()
    return _plex(request)


@router.post("/fragments/settings/libraries", response_class=HTMLResponse)
def save_libraries(request: Request, library_keys: Annotated[list[str] | None, Form()] = None):
    if not library_keys:  # keep the previous selection
        return _plex(request, 422, library_error="Select at least one library")
    request.app.state.store.set_setting("selected_library_keys", list(library_keys))
    return _plex(request, note="Saved")


@router.post("/fragments/settings/scan", response_class=HTMLResponse)
def save_scan_interval(request: Request, scan_interval_min: Text = None):
    interval = parse_setting_int("scan_interval_min", scan_interval_min)
    if interval is None:
        return _plex(request, 422, error=range_error("Scan interval", "scan_interval_min"), scan_interval=scan_interval_min)
    request.app.state.store.set_setting("scan_interval_min", interval)
    return _plex(request, note="Saved")


@router.post("/fragments/settings/scan/run", response_class=HTMLResponse)
async def scan_now(request: Request):
    result = await request.app.state.scheduler.scan_now()
    return _plex(request, note=None if result else "A scan is already running.")


# ---- friends ----


@router.post("/fragments/settings/friends/resolve", response_class=HTMLResponse)
async def resolve_friends(request: Request):
    if request.app.state.auth.community_client() is not None:
        await request.app.state.scheduler.resolve_now()
    store = request.app.state.store
    return templates.TemplateResponse(request, "partials/friends_card.html", {"authors": store.list_authors()})


@router.post("/fragments/settings/friends/monitor", response_class=HTMLResponse)
def monitor_friend(request: Request, hash: Annotated[str, Form()], monitored: Text = None):
    store = request.app.state.store
    store.set_author_monitored(hash, monitored is not None)
    a = next((x for x in store.list_authors() if x.hash == hash), None)
    if a is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/friend_row.html", {"a": a})


# ---- page ----


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    store = request.app.state.store
    counts = _counts(store)
    ctx = {
        "tab": None, "tabs": TABS, "page": "settings", "now": _now(), "counts": counts,
        "status": _header_status(store),
        "authors": store.list_authors(),
        **_feed_ctx(request),
        **_plex_ctx(request),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)
