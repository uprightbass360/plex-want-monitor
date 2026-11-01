import pytest

from app.matcher import Match, build_index, find_match
from app.plex_client import Library, PlexServerClient


@pytest.fixture
def index(transport):
    client = PlexServerClient("https://192-168-0-86.plex.direct:32400", "tok", transport.client)
    return build_index(
        [
            (Library("2", "movie", "Movies"), client.list_all("2")),
            (Library("6", "show", "TV Shows"), client.list_all("3")),
        ]
    )


def test_guid_match_wins_over_title(index):
    m = find_match("movie", "imdb://tt0096734", "Totally Different", None, index)
    assert m == Match("14696", "2", "Movies", "guid")


def test_title_year_fallback_and_type_scope(index):
    assert find_match("movie", "tvdb://x", "the burbs", 1989, index).method == "title"
    assert find_match("show", "tvdb://x", "The 'Burbs", 1989, index) is None
    assert find_match("movie", "tvdb://x", "The 'Burbs", 1990, index) is None
    assert find_match("movie", "tvdb://x", "The 'Burbs", None, index) is None


def test_entry_without_guids_or_year_is_indexed(index):
    assert any(e.title == "The Great Plague" for e in index.by_title[("movie", "the great plague")])
    assert find_match("movie", "imdb://none", "The Great Plague", None, index) is None


def test_rejected_keys_are_skipped(index):
    assert find_match("movie", "imdb://tt0096734", "The 'Burbs", 1989, index, rejected={"14696"}) is None


def test_guid_requires_same_type(index):
    assert find_match("show", "imdb://tt0096734", "x", None, index) is None


def test_rating_keys_union(index):
    assert "14696" in index.rating_keys
