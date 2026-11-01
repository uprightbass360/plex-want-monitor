from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.lifecycle import STATUSES, InvalidTransition, apply_not_a_match, apply_owner_transition
from app.models import SORTS, ItemFilter, item_to_dict
from app.plex_client import PlexError
from app.store import StaleStatus
from app.web.settings_shared import (
    CAP_MAX, CAP_MIN, DEFAULTS, INTERVAL_MAX, INTERVAL_MIN, mask_feed_url, select_server,
)

router = APIRouter()

STALE = "status changed, reload"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code)


def _item_dict(store, item) -> dict:
    return item_to_dict(item, store.authors_for([item.id]).get(item.id, []))


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/api/status")
def status(request: Request):
    store, auth = request.app.state.store, request.app.state.auth
    return jsonable_encoder(
        {
            "counts": store.counts_by_status(),
            "last_fetch": store.last_fetch(),
            "last_scan": store.last_scan(),
            "plex": auth.status(),
        }
    )


@router.post("/api/feed/fetch")
async def fetch_now(request: Request):
    result = await request.app.state.scheduler.fetch_now()
    return jsonable_encoder(result) if result else {"skipped": True}


@router.post("/api/plex/scan")
async def scan_now(request: Request):
    result = await request.app.state.scheduler.scan_now()
    return jsonable_encoder(result) if result else {"skipped": True}


# ---- items ----


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


def _build_filter(status, type, q, author, in_feed, sort) -> ItemFilter | JSONResponse:
    statuses = _csv(status)
    if any(st not in STATUSES for st in statuses):
        return _error(422, "unknown status")
    if type not in (None, "", "movie", "show"):
        return _error(422, "type must be movie or show")
    if sort is not None and sort not in SORTS:
        return _error(422, "unknown sort")
    if not statuses:
        statuses = ("review",)
        in_feed = True if in_feed is None else in_feed
    if sort is None:
        sort = "queue" if statuses == ("queued",) else "newest"
    return ItemFilter(statuses, type or None, q or None, author or None, in_feed, sort)


@router.get("/api/items")
def list_items(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    q: str | None = None,
    author: str | None = None,
    in_feed: bool | None = None,
    sort: str | None = None,
):
    store = request.app.state.store
    f = _build_filter(status, type, q, author, in_feed, sort)
    if isinstance(f, JSONResponse):
        return f
    items = store.list_items(f)
    authors = store.authors_for([i.id for i in items])
    return {"items": [item_to_dict(i, authors.get(i.id, [])) for i in items], "count": len(items)}


@router.get("/api/items/queue.txt")
def queue_txt(request: Request):
    store = request.app.state.store
    items = store.list_items(ItemFilter(("queued",), sort="queue"))
    lines = [item_to_dict(i, [])["search_string"] for i in items]
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="text/plain; charset=utf-8")


class StatusBody(BaseModel):
    status: str


class BulkBody(BaseModel):
    ids: list[int]
    status: str


def _transition(store, item_id: int, target: str):
    """Returns (item_dict, None) or (None, (http_code, message))."""
    try:
        item = apply_owner_transition(store, item_id, target, _now())
    except KeyError:
        return None, (404, "item not found")
    except InvalidTransition as exc:
        return None, (409, str(exc))
    except StaleStatus:
        return None, (409, STALE)
    return _item_dict(store, item), None


@router.post("/api/items/bulk")
def bulk_status(request: Request, body: BulkBody):
    store = request.app.state.store
    updated, skipped = [], []
    for item_id in body.ids:
        item, err = _transition(store, item_id, body.status)
        if err:
            skipped.append({"id": item_id, "error": err[1]})
        else:
            updated.append(item)
    return {"updated": updated, "skipped": skipped}


@router.post("/api/items/{item_id}/status")
def set_status(request: Request, item_id: int, body: StatusBody):
    item, err = _transition(request.app.state.store, item_id, body.status)
    return _error(*err) if err else item


@router.post("/api/items/{item_id}/skip")
def skip_item(request: Request, item_id: int):
    store = request.app.state.store
    if store.get_item(item_id) is None:
        return _error(404, "item not found")
    return _item_dict(store, store.skip(item_id, _now()))


@router.post("/api/items/{item_id}/not-a-match")
def not_a_match(request: Request, item_id: int):
    store = request.app.state.store
    try:
        item = apply_not_a_match(store, item_id, _now())
    except KeyError:
        return _error(404, "item not found")
    except InvalidTransition as exc:
        return _error(409, str(exc))
    except StaleStatus:
        return _error(409, STALE)
    return _item_dict(store, item)


# ---- settings ----

_DEFAULTS = DEFAULTS


class Friend(BaseModel):
    hash: str
    label: str | None = None


class SettingsBody(BaseModel):
    feed_url: str | None = None
    feed_interval_min: int | None = Field(None, ge=INTERVAL_MIN, le=INTERVAL_MAX)
    scan_interval_min: int | None = Field(None, ge=INTERVAL_MIN, le=INTERVAL_MAX)
    feed_cap_threshold: int | None = Field(None, ge=CAP_MIN, le=CAP_MAX)
    selected_library_keys: list[str] | None = None
    friends: list[Friend] | None = None


def _settings_view(store) -> dict:
    url = store.get_setting("feed_url")
    return {
        "feed_url_masked": mask_feed_url(url),
        **{k: store.get_setting(k, d) for k, d in _DEFAULTS.items()},
        "selected_library_keys": store.get_setting("selected_library_keys", []),
        "friends": [
            {
                "hash": a.hash, "label": a.label, "username": a.username,
                "display_name": a.display_name, "avatar_url": a.avatar_url,
                "name": a.name, "item_count": a.item_count,
            }
            for a in store.list_authors()
        ],
    }


@router.get("/api/settings")
def get_settings(request: Request):
    return _settings_view(request.app.state.store)


@router.put("/api/settings")
def put_settings(request: Request, body: SettingsBody):
    store = request.app.state.store
    if body.feed_url:
        store.set_setting("feed_url", body.feed_url)
    for key in (*_DEFAULTS, "selected_library_keys"):
        value = getattr(body, key)
        if value is not None:
            store.set_setting(key, value)
    for friend in body.friends or []:
        store.set_author_label(friend.hash, friend.label)
    return _settings_view(store)


@router.post("/api/friends/resolve")
async def friends_resolve(request: Request):
    app = request.app
    if app.state.auth.community_client() is None:
        return _error(409, "not signed in")
    return {"resolved": await app.state.scheduler.resolve_now()}


# ---- plex ----

_NOT_SIGNED_IN = "not signed in"


def _plex_call(fn):
    try:
        return fn()
    except PlexError as exc:
        return _error(502, str(exc))


@router.post("/api/plex/pin")
def plex_pin_start(request: Request):
    pin = request.app.state.auth.start_pin()
    return {"code": pin.code, "expires_at": pin.expires_at.isoformat()}


@router.get("/api/plex/pin")
def plex_pin_poll(request: Request):
    return jsonable_encoder(request.app.state.auth.poll_pin())


@router.get("/api/plex/servers")
def plex_servers(request: Request):
    auth = request.app.state.auth
    if not auth.status()["signed_in"]:
        return _error(409, _NOT_SIGNED_IN)
    return _plex_call(
        lambda: [
            {"name": r.name, "machine_id": r.machine_id, "owned": r.owned, "version": r.version}
            for r in auth.list_servers()
        ]
    )


class ServerBody(BaseModel):
    machine_id: str


@router.post("/api/plex/server")
def plex_select_server(request: Request, body: ServerBody):
    auth = request.app.state.auth
    if not auth.status()["signed_in"]:
        return _error(409, _NOT_SIGNED_IN)
    store = request.app.state.store
    return _plex_call(lambda: (select_server(store, auth, body.machine_id), auth.status())[1])


@router.post("/api/plex/reconnect")
def plex_reconnect(request: Request):
    auth = request.app.state.auth
    if not auth.status()["signed_in"]:
        return _error(409, _NOT_SIGNED_IN)
    return _plex_call(lambda: (auth.reconnect(), auth.status())[1])


@router.post("/api/plex/signout")
def plex_signout(request: Request):
    auth = request.app.state.auth
    auth.signout()
    return auth.status()


@router.get("/api/plex/libraries")
def plex_libraries(request: Request):
    client = request.app.state.auth.server_client()
    if client is None:
        return _error(409, _NOT_SIGNED_IN)
    return _plex_call(lambda: [{"key": s.key, "type": s.type, "title": s.title} for s in client.sections()])
