"""HTML pages and htmx fragments for the list tabs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.lifecycle import InvalidTransition, apply_not_a_match, apply_owner_transition
from app.models import ItemFilter, item_to_dict
from app.store import StaleStatus

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

NOT_A_MATCH = "not-a-match"
LIST_SORTS = ("newest", "oldest", "title", "title_desc", "type", "type_desc")
QUEUE = "queue"


@dataclass(frozen=True)
class Action:
    label: str
    status: str  # target status, or NOT_A_MATCH
    style: str = ""


@dataclass(frozen=True)
class Tab:
    key: str
    label: str
    statuses: tuple[str, ...]
    actions: tuple[Action, ...]
    empty: str
    page: str = "list.html"
    rows: str = "partials/rows.html"
    row: str = "partials/row.html"
    age_label: str = "On watchlist since"
    age_key: str = "since"  # key in the row dict holding the datetime shown in the age column


TABS: dict[str, Tab] = {
    t.key: t
    for t in (
        Tab("review", "Review", ("review",),
            (Action("Queue", "queued", "q"), Action("Ignore", "ignored", "warn")),
            "Nothing to review. New watchlist items show up here."),
        Tab(QUEUE, "Queue", ("queued",), (), "Nothing queued. Queue items from Review.",
            "queue.html", "partials/queue_rows.html", "partials/queue_row.html"),
        Tab("grabbed", "Grabbed", ("grabbed",), (Action("Undo", "queued"),),
            "Nothing grabbed and waiting. Items move to In library once Plex has them.",
            age_label="Grabbed", age_key="changed_at"),
        Tab("library", "In library", ("library",), (Action("Not a match", NOT_A_MATCH),),
            "Nothing matched in Plex yet.", age_label="Matched", age_key="matched_dt"),
        Tab("ignored", "Ignored", ("ignored",), (Action("Unignore", "review"),),
            "Nothing ignored."),
    )
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "never"
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 48:
        return "1 h ago" if hours == 1 else f"{hours} h ago"
    days = hours // 24
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    months = days // 30
    if months < 24:
        return f"{months} months ago"
    return f"{months // 12} years ago"


templates.env.filters["ago"] = ago


# ---- view-model helpers ----


def _tab(key: str | None) -> Tab:
    tab = TABS.get(key or "")
    if tab is None:
        raise HTTPException(404, "unknown tab")
    return tab


@dataclass(frozen=True)
class Filters:
    type: str = "all"
    q: str = ""
    author: str = ""
    show_removed: bool = False
    sort: str = "newest"

    def query(self, **overrides) -> str:
        """Query string for this tab's page URL, with overrides applied."""
        values = {
            "type": self.type, "q": self.q, "author": self.author,
            "show_removed": "1" if self.show_removed else "", "sort": self.sort,
        }
        values.update(overrides)
        defaults = {"type": "all", "sort": "newest"}
        return urlencode({k: v for k, v in values.items() if v and defaults.get(k) != v})


def _filters(type=None, q=None, author=None, show_removed=None, sort=None) -> Filters:
    type = type or "all"
    sort = sort or "newest"
    if type not in ("all", "movie", "show"):
        raise HTTPException(422, "type must be all, movie or show")
    if sort not in LIST_SORTS:
        raise HTTPException(422, "unknown sort")
    return Filters(type, (q or "").strip(), author or "", show_removed == "1", sort)


def _rows_parts(tab: Tab) -> list[str]:
    return [tab.rows, "partials/json_link.html"]


def _item_filter(tab: Tab, f: Filters) -> ItemFilter:
    if tab.key == QUEUE:  # fixed queue order; shown regardless of in_feed
        return ItemFilter(tab.statuses, None if f.type == "all" else f.type, f.q or None,
                          f.author or None, None, "queue")
    in_feed = True if tab.key == "review" and not f.show_removed else None
    return ItemFilter(tab.statuses, None if f.type == "all" else f.type, f.q or None,
                      f.author or None, in_feed, f.sort)


def _json_href(tab: Tab, f: Filters) -> str:
    params = {"status": ",".join(tab.statuses)}
    if tab.key == "review" and not f.show_removed:
        params["in_feed"] = "true"
    if f.type != "all":
        params["type"] = f.type
    params.update({k: v for k, v in (("q", f.q), ("author", f.author)) if v})
    params["sort"] = "queue" if tab.key == QUEUE else f.sort
    return "/api/items?" + urlencode(params)


def _counts(store) -> dict[str, int]:
    by_status = store.counts_by_status()
    counts = {key: sum(by_status.get(s, 0) for s in tab.statuses) for key, tab in TABS.items()}
    counts["review"] = len(store.list_items(ItemFilter(("review",), in_feed=True)))
    return counts


def _not_rescanned(item, scan, selected) -> str | None:
    """Why the latest scan could not confirm this library item's match, or None."""
    if item.status != "library" or scan is None or not scan.machine_id:
        return None
    if item.plex_machine_id != scan.machine_id:
        return "server changed"
    if selected and item.plex_library_key not in selected:
        return "library not selected"
    entry = next((lib for lib in scan.libraries if lib.get("key") == item.plex_library_key), None)
    if entry is not None and entry.get("selected") is False:
        return "library not selected"
    if entry is not None and entry.get("ok") is False:
        return "library listing failed"
    if entry is not None and entry.get("ok") and entry.get("count") == 0:
        return "library listed empty"
    return None


def _rows(store, items) -> list[dict]:
    authors = store.authors_for([i.id for i in items])
    scan = store.last_scan() if any(i.status == "library" for i in items) else None
    selected = store.get_setting("selected_library_keys") or None
    return [
        {
            **item_to_dict(i, authors.get(i.id, [])),
            "since": i.pub_date or i.first_seen,
            "dated": i.pub_date is not None,
            "changed_at": i.status_changed_at,
            "matched_dt": i.matched_at,
            "not_rescanned": _not_rescanned(i, scan, selected),
        }
        for i in items
    ]


def _header_status(store) -> dict:
    fetch, scan, wl = store.last_fetch(), store.last_scan(), store.last_fetch(source="watchlist")
    errors = [log.error for log in (fetch, wl, scan) if log and not log.ok and log.error]
    times = {"feed": fetch, "watchlist": wl, "scan": scan}
    stamps = [log.finished_at for log in times.values() if log and log.finished_at]
    now = _now()
    detail = " · ".join(
        f"{name} {ago(log.finished_at if log else None, now)}"
        for name, log in times.items() if name != "watchlist" or wl
    )
    return {
        "updated_at": max(stamps) if stamps else None,
        "detail": detail,
        "fetched_at": fetch.finished_at if fetch else None,
        "watchlisted_at": wl.finished_at if wl else None,
        "scanned_at": scan.finished_at if scan else None,
        "truncated": bool(fetch and fetch.truncated),
        "errors": errors,
    }


def _context(request: Request, tab: Tab, f: Filters, **extra) -> dict:
    store = request.app.state.store
    people = store.list_authors()
    by_tab = store.count_by_author(_item_filter(tab, f))
    return {
        "tab": tab, "tabs": TABS, "f": f, "now": _now(),
        "counts": _counts(store),
        "json_href": _json_href(tab, f),
        "authors": {a.hash: a.name for a in people},
        "author_options": [
            (a, by_tab.get(a.hash, 0)) for a in sorted(people, key=lambda a: a.name.casefold())
        ],
        **extra,
    }


def _list_context(request: Request, tab: Tab, f: Filters, **extra) -> dict:
    store = request.app.state.store
    rows = _rows(store, store.list_items(_item_filter(tab, f)))
    return _context(request, tab, f, rows=rows, **extra)


def _render(names: list[str], ctx: dict, **extra) -> HTMLResponse:
    """Concatenate several templates into one response (main content plus out-of-band parts)."""
    ctx = {**ctx, **extra}
    return HTMLResponse("".join(templates.get_template(n).render(ctx) for n in names))


def _error_fragment(request: Request, code: int, message: str) -> HTMLResponse:
    response = templates.TemplateResponse(
        request, "partials/error.html", {"message": message}, status_code=code
    )
    response.headers["HX-Retarget"] = "#flash"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


def _apply(store, item_id: int, target: str):
    if target == NOT_A_MATCH:
        return apply_not_a_match(store, item_id, _now())
    return apply_owner_transition(store, item_id, target, _now())


def _row_response(request: Request, tab: Tab, item_id: int, target: str) -> HTMLResponse:
    store = request.app.state.store
    try:
        item = _apply(store, item_id, target)
    except KeyError:
        return _error_fragment(request, 404, "That item no longer exists.")
    except InvalidTransition as exc:
        return _error_fragment(request, 409, str(exc))
    except StaleStatus:
        return _error_fragment(request, 409, "status changed, reload")
    ctx = _context(request, tab, Filters())
    # Queue "Got it" keeps the row on screen, faded, with an Undo button.
    done = tab.key == QUEUE and item.status == "grabbed"
    names = [tab.row] if done or item.status in tab.statuses else []
    return _render([*names, "partials/counts.html"], ctx, row=_rows(store, [item])[0], oob=True, done=done)


# ---- routes ----

Text = Annotated[str | None, Form()]


@router.get("/")
def root():
    return RedirectResponse("/review", status_code=302)


@router.get("/fragments/header-status", response_class=HTMLResponse)
def header_status(request: Request):
    return templates.TemplateResponse(
        request, "partials/header_status.html",
        {"status": _header_status(request.app.state.store), "now": _now()},
    )


def _refreshed(request: Request) -> HTMLResponse:
    """Header status plus out-of-band tab counts, and an event that reloads the visible cards and rows."""
    store = request.app.state.store
    path = urlparse(request.headers.get("HX-Current-URL", "")).path.strip("/")
    ctx = {
        "status": _header_status(store), "now": _now(), "tabs": TABS, "counts": _counts(store),
        "tab": TABS.get(path), "page": "settings" if path == "settings" else None,
    }
    response = _render(["partials/header_status.html", "partials/counts.html"], ctx)
    response.headers["HX-Trigger"] = "wm-refreshed"
    return response


@router.post("/fragments/refresh", response_class=HTMLResponse)
async def refresh(request: Request):
    scheduler = request.app.state.scheduler
    await scheduler.fetch_now()
    await scheduler.scan_now()
    return _refreshed(request)


@router.post("/fragments/scan", response_class=HTMLResponse)
async def scan(request: Request):
    await request.app.state.scheduler.scan_now()
    return _refreshed(request)


@router.get("/fragments/{tab_key}/rows", response_class=HTMLResponse)
def rows_fragment(request: Request, tab_key: str, type: str | None = None, q: str | None = None,
                  author: str | None = None, show_removed: str | None = None, sort: str | None = None):
    tab = _tab(tab_key)
    f = _filters(type, q, author, show_removed, sort)
    response = _render(_rows_parts(tab), _list_context(request, tab, f), oob=True)
    query = f.query()
    response.headers["HX-Push-Url"] = f"/{tab.key}" + (f"?{query}" if query else "")
    return response


@router.post("/fragments/items/bulk", response_class=HTMLResponse)
def bulk(request: Request, status: Annotated[str, Form()], tab: Annotated[str, Form()],
         ids: Annotated[list[int] | None, Form()] = None, type: Text = None, q: Text = None,
         author: Text = None, show_removed: Text = None, sort: Text = None):
    current = _tab(tab)
    f = _filters(type, q, author, show_removed, sort)
    store = request.app.state.store
    skipped = 0
    for item_id in ids or []:
        try:
            _apply(store, item_id, status)
        except (KeyError, InvalidTransition, StaleStatus):
            skipped += 1
    ctx = _list_context(request, current, f, skipped=skipped)
    return _render([*_rows_parts(current), "partials/counts.html"], ctx, oob=True)


@router.post("/fragments/items/{item_id}/status", response_class=HTMLResponse)
def status_fragment(request: Request, item_id: int, status: Annotated[str, Form()],
                    tab: Annotated[str, Form()]):
    return _row_response(request, _tab(tab), item_id, status)


@router.post("/fragments/items/{item_id}/skip", response_class=HTMLResponse)
def skip_fragment(request: Request, item_id: int, tab: Annotated[str, Form()] = QUEUE,
                  type: Text = None, q: Text = None, author: Text = None):
    current = _tab(tab)
    store = request.app.state.store
    item = store.get_item(item_id)
    if item is None:
        return _error_fragment(request, 404, "That item no longer exists.")
    if item.status != "queued":
        return _error_fragment(request, 409, "Only queued items can be skipped.")
    item = store.skip(item_id, _now())
    # Re-render only this row and move it to the end of #rows; other rows (faded done ones too) stay.
    ctx = _context(request, current, _filters(type, q, author))
    response = _render([current.row, "partials/oob_delete.html"], ctx, row=_rows(store, [item])[0], done=False)
    response.headers["HX-Reswap"] = "beforeend"
    response.headers["HX-Retarget"] = "#rows"
    return response


@router.post("/fragments/items/{item_id}/not-a-match", response_class=HTMLResponse)
def not_a_match_fragment(request: Request, item_id: int, tab: Annotated[str, Form()] = "library"):
    return _row_response(request, _tab(tab), item_id, NOT_A_MATCH)


# Keep last: the catch-all tab route would shadow any single-segment path below it.
@router.get("/{tab_key}", response_class=HTMLResponse)
def list_page(request: Request, tab_key: str, type: str | None = None, q: str | None = None,
              author: str | None = None, show_removed: str | None = None, sort: str | None = None):
    tab = _tab(tab_key)
    f = _filters(type, q, author, show_removed, sort)
    status = _header_status(request.app.state.store)
    return templates.TemplateResponse(request, tab.page, _list_context(request, tab, f, status=status))
