import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

MEDIA_NS = "{http://search.yahoo.com/mrss/}"
_YEAR_SUFFIX = re.compile(r"^(.*\S)\s+\((\d{4})\)$")


class FeedParseError(ValueError):
    pass


@dataclass(frozen=True)
class FeedItem:
    guid: str
    type: str
    raw_title: str
    title: str
    year: int | None
    link: str | None
    thumb: str | None
    keywords: tuple[str, ...]
    rating: str | None
    pub_date: datetime | None
    author_hash: str | None


@dataclass(frozen=True)
class Feed:
    title: str
    items: tuple[FeedItem, ...]


def split_title_year(raw: str) -> tuple[str, int | None]:
    m = _YEAR_SUFFIX.match(raw.strip())
    if m:
        return m.group(1), int(m.group(2))
    return raw.strip(), None


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_item(el: ET.Element) -> FeedItem | None:
    link = _text(el, "link")
    guid = _text(el, "guid") or link
    if not guid:
        return None
    raw_title = _text(el, "title") or ""
    title, year = split_title_year(raw_title)
    category = _text(el, "category")
    thumb_el = el.find(f"{MEDIA_NS}thumbnail")
    keywords = _text(el, f"{MEDIA_NS}keywords") or ""
    return FeedItem(
        guid=guid,
        type=category if category in ("movie", "show") else "movie",
        raw_title=raw_title,
        title=title,
        year=year,
        link=link,
        thumb=thumb_el.get("url") if thumb_el is not None else None,
        keywords=tuple(k.strip() for k in keywords.split(",") if k.strip()),
        rating=_text(el, f"{MEDIA_NS}rating"),
        pub_date=_parse_date(_text(el, "pubDate")),
        author_hash=_text(el, "author"),
    )


def parse_feed(data: bytes) -> Feed:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedParseError(f"not valid XML: {e}") from e
    if root.tag != "rss":
        raise FeedParseError(f"not an RSS feed (root <{root.tag}>)")
    channel = root.find("channel")
    if channel is None:
        raise FeedParseError("RSS feed has no channel")
    items = tuple(i for i in map(_parse_item, channel.findall("item")) if i)
    return Feed(title=_text(channel, "title") or "", items=items)
