import pytest

from app.normalize import normalize_title, search_string


@pytest.mark.parametrize("title,key", [
    ("The 'Burbs", "the burbs"), ("Dune: Part Two", "dune part two"),
    ("12 Angry Men", "12 angry men"), ("Amélie", "amelie"), ("WALL·E", "wall e"),
])
def test_normalize_title(title, key):
    assert normalize_title(title) == key


@pytest.mark.parametrize("t,title,year,expected", [
    ("movie", "Dune: Part Three", 2026, "Dune Part Three 2026"),
    ("movie", "The 'Burbs", 1989, "The Burbs 1989"),
    ("movie", "Amélie", 2001, "Amélie 2001"),
    ("movie", "Untitled", None, "Untitled"),
    ("show", "Severance", 2022, "Severance"),
])
def test_search_string(t, title, year, expected):
    assert search_string(t, title, year) == expected
