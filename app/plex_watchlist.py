"""The owner's own Plex watchlist (discover.provider.plex.tv) as a second item source."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.feed import FeedItem
from app.models import FetchLog
from app.plex_client import PLEX_HEADERS, PlexError, _get_json

log = logging.getLogger(__name__)

DISCOVER = "https://discover.provider.plex.tv"
WATCHLIST_TIMEOUT = 10.0
PAGE_SIZE = 100
MAX_PAGES = 100  # safety stop: 10 000 entries


@dataclass(frozen=True)
class WatchlistEntry:
    rating_key: str
    plex_guid: str
    type: str
    title: str
    year: int | None
    added_at: datetime | None
    thumb: str | None
    content_rating: str | None = None


class PlexWatchlist:
    def __init__(self, http: httpx.Client, token: str, client_id: str):
        self.http = http
        self.token = token
        self.client_id = client_id

    def _get(self, path: str) -> dict:
        headers = {**PLEX_HEADERS, "X-Plex-Client-Identifier": self.client_id, "X-Plex-Token": self.token}
        d = _get_json(self.http, "GET", f"{DISCOVER}{path}", headers, WATCHLIST_TIMEOUT)
        if not isinstance(d, dict) or not isinstance(d.get("MediaContainer"), dict):
            raise PlexError("discover.provider.plex.tv returned an unexpected response")
        return d["MediaContainer"]

    def items(self) -> list[WatchlistEntry]:
        out: list[WatchlistEntry] = []
        start = 0
        for _ in range(MAX_PAGES):
            mc = self._get(
                "/library/sections/watchlist/all"
                f"?X-Plex-Container-Start={start}&X-Plex-Container-Size={PAGE_SIZE}"
            )
            page = mc.get("Metadata") or []
            try:
                out.extend(_entry(m) for m in page)
                total = int(mc.get("totalSize", len(out)))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise PlexError("watchlist response is incomplete") from None
            start += len(page)
            if not page or start >= total:
                break
        return out

    def external_guids(self, rating_key: str) -> tuple[str, ...]:
        mc = self._get(f"/library/metadata/{quote(rating_key, safe='')}")
        try:
            meta = (mc.get("Metadata") or [{}])[0]
            return tuple(g["id"] for g in meta.get("Guid") or [])
        except (KeyError, TypeError, IndexError, AttributeError):
            raise PlexError("watchlist metadata response is incomplete") from None


def _entry(m: dict) -> WatchlistEntry:
    added = m.get("addedAt")
    return WatchlistEntry(
        rating_key=str(m["ratingKey"]),
        plex_guid=m["guid"],
        type=m["type"] if m.get("type") in ("movie", "show") else "movie",
        title=m["title"],
        year=m.get("year"),
        added_at=datetime.fromtimestamp(int(added), UTC) if added else None,
        thumb=m.get("thumb") or None,
        content_rating=m.get("contentRating") or None,
    )


def _first_external(guids: tuple[str, ...]) -> str | None:
    return next((g for g in guids if g and not g.startswith("plex://")), None)


def run_watchlist_sync(store, auth, now: datetime) -> FetchLog:
    """Sync the owner's watchlist into items. Never raises; a failure leaves item rows untouched.
    No-ops (signed out, disabled) are returned but not logged."""

    def noop(error: str) -> FetchLog:
        return FetchLog(now, now, False, error=error, source="watchlist")

    def finish(**kw) -> FetchLog:
        entry = FetchLog(started_at=now, finished_at=now, source="watchlist", **kw)
        entry.id = store.log_fetch(entry)
        return entry

    if not store.get_setting("own_watchlist_enabled", True):
        return noop("own watchlist disabled")
    client = auth.watchlist_client()
    if client is None:
        return noop("not signed in")
    try:
        entries = client.items()
        feed_items: list[FeedItem] = []
        owner = auth.owner_uuid()
        for e in entries:
            guid = store.get_watchlist_guid(e.rating_key)
            if guid is None:
                guid = _first_external(client.external_guids(e.rating_key)) or e.plex_guid
                store.set_watchlist_guid(e.rating_key, guid, now)
            feed_items.append(FeedItem(
                guid=guid, type=e.type, raw_title=e.title, title=e.title, year=e.year, link=None,
                thumb=e.thumb, keywords=(), rating=e.content_rating, pub_date=e.added_at, author_hash=owner,
            ))
        new, _ = store.upsert_feed_items(feed_items, now, source="watchlist")
        store.mark_left_feed({i.guid for i in feed_items}, now, source="watchlist")
    except PlexError as e:
        return finish(ok=False, error=str(e))
    except Exception as e:  # never echo the text: keep the log free of request details
        log.warning("watchlist sync failed: %s", type(e).__name__)
        return finish(ok=False, error=f"watchlist sync failed: {type(e).__name__}")
    return finish(ok=True, item_count=len(feed_items), new_count=len(new))
