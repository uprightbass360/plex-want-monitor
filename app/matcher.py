from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.normalize import normalize_title
from app.plex_client import Library, PlexEntry


@dataclass(frozen=True)
class IndexedEntry:
    rating_key: str
    library_key: str
    library_name: str
    title: str
    year: int | None
    type: str


@dataclass(frozen=True)
class LibraryIndex:
    by_guid: dict[str, IndexedEntry]
    by_title: dict[tuple[str, str], list[IndexedEntry]]
    rating_keys: frozenset[str]


@dataclass(frozen=True)
class Match:
    rating_key: str
    library_key: str
    library_name: str
    method: Literal["guid", "title"]


def build_index(listings: Iterable[tuple[Library, Sequence[PlexEntry]]]) -> LibraryIndex:
    by_guid: dict[str, IndexedEntry] = {}
    by_title: dict[tuple[str, str], list[IndexedEntry]] = {}
    keys: set[str] = set()
    for lib, entries in listings:
        for e in entries:
            ie = IndexedEntry(e.rating_key, lib.key, lib.title, e.title, e.year, e.type or lib.type)
            keys.add(e.rating_key)
            for g in (*e.guids, *([e.plex_guid] if e.plex_guid else [])):
                by_guid.setdefault(g, ie)
            by_title.setdefault((ie.type, normalize_title(e.title)), []).append(ie)
    return LibraryIndex(by_guid, by_title, frozenset(keys))


def find_match(
    item_type: str,
    guid: str,
    title: str,
    year: int | None,
    index: LibraryIndex,
    rejected: Collection[str] = (),
) -> Match | None:
    hit = index.by_guid.get(guid)
    if hit and hit.type == item_type and hit.rating_key not in rejected:
        return Match(hit.rating_key, hit.library_key, hit.library_name, "guid")
    if year is None:
        return None
    for e in index.by_title.get((item_type, normalize_title(title)), ()):
        if e.year == year and e.rating_key not in rejected:
            return Match(e.rating_key, e.library_key, e.library_name, "title")
    return None
