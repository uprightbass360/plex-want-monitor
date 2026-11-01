import pytest

from app.lifecycle import (
    STATUSES, InvalidTransition, apply_not_a_match, apply_owner_transition,
    check_transition, not_a_match_target,
)


@pytest.mark.parametrize("cur,tgt", [("review","queued"),("review","ignored"),("queued","grabbed"),
    ("queued","review"),("queued","ignored"),("grabbed","queued"),("ignored","review")])
def test_owner_allowed(cur, tgt):
    check_transition(cur, tgt, "owner")


@pytest.mark.parametrize("cur,tgt", [("review","grabbed"),("library","queued"),("grabbed","ignored"),
    ("ignored","queued"),("review","library"),("review","bogus"),("bogus","review")])
def test_owner_rejected(cur, tgt):
    with pytest.raises(InvalidTransition):
        check_transition(cur, tgt, "owner")


def test_scan_may_set_library_from_anything_and_revert_to_review():
    for s in STATUSES:
        check_transition(s, "library", "scan")
    check_transition("library", "review", "scan")
    with pytest.raises(InvalidTransition):
        check_transition("queued", "grabbed", "scan")


def test_not_a_match_restores_previous(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    apply_owner_transition(store, ids[0], "queued", now)
    store.set_match(ids[0], "55", "2", "Movies", "m", "title", now)
    store.set_status(ids[0], "library", now)
    item = apply_not_a_match(store, ids[0], now)
    assert item.status == "queued" and item.plex_rating_key is None and item.rejected_rating_keys == ("55",)
    assert item.status_note == "not a match"
    assert not_a_match_target(None) == "review"


def test_not_a_match_requires_library(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    with pytest.raises(InvalidTransition):
        apply_not_a_match(store, ids[0], now)


def test_owner_transition_rejects_invalid(store, feed_items, now):
    ids, _ = store.upsert_feed_items(feed_items, now)
    with pytest.raises(InvalidTransition):
        apply_owner_transition(store, ids[0], "grabbed", now)
