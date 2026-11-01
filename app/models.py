from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.normalize import search_string

SORTS = ("newest", "oldest", "title", "queue")


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class Item:
    id: int
    guid: str
    type: str
    title: str
    raw_title: str
    year: int | None
    link: str | None
    thumb: str | None
    keywords: tuple[str, ...]
    rating: str | None
    pub_date: datetime | None
    first_seen: datetime
    last_seen: datetime
    in_feed: bool
    left_feed_at: datetime | None
    status: str
    previous_status: str | None
    status_changed_at: datetime
    status_note: str | None
    queue_order: int | None
    plex_rating_key: str | None
    plex_library_key: str | None
    plex_library_name: str | None
    plex_machine_id: str | None
    match_method: str | None
    matched_at: datetime | None
    rejected_rating_keys: tuple[str, ...]
    source: str = "feed"

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Item:
        return cls(
            id=r["id"], guid=r["guid"], type=r["type"], title=r["title"],
            raw_title=r["raw_title"], year=r["year"], link=r["link"], thumb=r["thumb"],
            keywords=tuple(k for k in (r["keywords"] or "").split(",") if k),
            rating=r["rating"], pub_date=from_iso(r["pub_date"]),
            first_seen=from_iso(r["first_seen"]), last_seen=from_iso(r["last_seen"]),
            in_feed=bool(r["in_feed"]), left_feed_at=from_iso(r["left_feed_at"]),
            status=r["status"], previous_status=r["previous_status"],
            status_changed_at=from_iso(r["status_changed_at"]), status_note=r["status_note"],
            queue_order=r["queue_order"], plex_rating_key=r["plex_rating_key"],
            plex_library_key=r["plex_library_key"], plex_library_name=r["plex_library_name"],
            plex_machine_id=r["plex_machine_id"], match_method=r["match_method"],
            matched_at=from_iso(r["matched_at"]),
            rejected_rating_keys=tuple(json.loads(r["rejected_rating_keys"] or "[]")),
            source=r["source"],
        )


@dataclass
class Author:
    hash: str
    label: str | None
    item_count: int
    last_seen: datetime | None
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    resolved_at: datetime | None = None
    monitored: bool = True

    @property
    def name(self) -> str:
        return self.display_name or self.username or self.label or self.hash[:6]


@dataclass
class FetchLog:
    started_at: datetime
    finished_at: datetime
    ok: bool
    id: int | None = None
    error: str | None = None
    status_code: int | None = None
    item_count: int = 0
    new_count: int = 0
    truncated: bool = False
    source: str = "feed"


@dataclass
class ScanLog:
    started_at: datetime
    finished_at: datetime
    ok: bool
    id: int | None = None
    error: str | None = None
    machine_id: str | None = None
    libraries: list[dict] = field(default_factory=list)
    indexed: int = 0
    matched: int = 0
    reverted: int = 0


@dataclass
class ItemFilter:
    statuses: tuple[str, ...] | None = None
    type: str | None = None
    q: str | None = None
    author: str | None = None
    in_feed: bool | None = None
    sort: str = "newest"
    monitored_only: bool = True  # hide items whose every author is unmonitored


def _iso_out(dt: datetime | None) -> str | None:
    return to_iso(dt)


def item_to_dict(item: Item, authors: list[Author]) -> dict:
    url = None
    if item.plex_rating_key and item.plex_machine_id:
        url = (
            f"https://app.plex.tv/desktop/#!/server/{item.plex_machine_id}/details"
            f"?key=%2Flibrary%2Fmetadata%2F{item.plex_rating_key}"
        )
    return {
        "id": item.id,
        "guid": item.guid,
        "source": item.source,
        "type": item.type,
        "title": item.title,
        "raw_title": item.raw_title,
        "year": item.year,
        "link": item.link,
        "thumb": item.thumb,
        "keywords": list(item.keywords),
        "rating": item.rating,
        "pub_date": _iso_out(item.pub_date),
        "first_seen": _iso_out(item.first_seen),
        "last_seen": _iso_out(item.last_seen),
        "in_feed": item.in_feed,
        "left_feed_at": _iso_out(item.left_feed_at),
        "status": item.status,
        "previous_status": item.previous_status,
        "status_changed_at": _iso_out(item.status_changed_at),
        "status_note": item.status_note,
        "queue_order": item.queue_order,
        "plex_rating_key": item.plex_rating_key,
        "plex_library_key": item.plex_library_key,
        "plex_library_name": item.plex_library_name,
        "plex_library": item.plex_library_name,  # spec name; alias of plex_library_name
        "plex_machine_id": item.plex_machine_id,
        "match_method": item.match_method,
        "matched_at": _iso_out(item.matched_at),
        "rejected_rating_keys": list(item.rejected_rating_keys),
        "search_string": search_string(item.type, item.title, item.year),
        "plex_web_url": url,
        "authors": [
            {
                "hash": a.hash, "label": a.label, "username": a.username,
                "display_name": a.display_name, "avatar_url": a.avatar_url, "name": a.name,
            }
            for a in authors
        ],
    }
