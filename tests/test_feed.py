from datetime import UTC, datetime

import pytest

from app.feed import FeedParseError, parse_feed, split_title_year


def rss_with_items(inner: str) -> bytes:
    return (
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:media="http://search.yahoo.com/mrss/"><channel>'
        "<title>Plex Watchlist</title>" + inner + "</channel></rss>"
    ).encode()


def test_parses_fixture(fixture_bytes):
    feed = parse_feed(fixture_bytes)
    assert feed.title == "Plex Watchlist" and len(feed.items) == 5
    first = feed.items[0]
    assert first.guid == "imdb://tt0080421" and first.type == "movie"
    assert (first.title, first.year) == ("Battle Beyond the Stars", 1980)
    assert first.keywords == ("science fiction", "action", "adventure")
    assert first.rating == "pg" and first.author_hash == "friend-a"
    assert first.pub_date == datetime(2026, 9, 11, 4, 41, 14, tzinfo=UTC)
    assert feed.items[4].type == "show"


def test_guid_falls_back_to_link_and_skips_items_with_neither():
    xml = rss_with_items('<item><title>A (2000)</title><link>https://x/a</link><category>movie</category></item>'
                         '<item><title>B (2001)</title><category>movie</category></item>')
    items = parse_feed(xml).items
    assert [i.guid for i in items] == ["https://x/a"]


def test_not_rss_raises():
    with pytest.raises(FeedParseError):
        parse_feed(b"<!doctype html><html><body>Attention Required</body></html>")


@pytest.mark.parametrize("raw,expected", [
    ("Battle Beyond the Stars (1980)", ("Battle Beyond the Stars", 1980)),
    ("2001: A Space Odyssey (1968)", ("2001: A Space Odyssey", 1968)),
    ("Blade Runner 2049 (2017)", ("Blade Runner 2049", 2017)),
    ("Severance", ("Severance", None)),
    ("Weird (Title) Here (1999)", ("Weird (Title) Here", 1999)),
])
def test_split_title_year(raw, expected):
    assert split_title_year(raw) == expected
