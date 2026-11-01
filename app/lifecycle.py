from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.models import Item

STATUSES: tuple[str, ...] = ("review", "queued", "grabbed", "library", "ignored")

OWNER_TRANSITIONS: dict[str, frozenset[str]] = {
    "review": frozenset({"queued", "ignored"}),
    "queued": frozenset({"grabbed", "review", "ignored"}),
    "grabbed": frozenset({"queued"}),
    "library": frozenset(),
    "ignored": frozenset({"review"}),
}


class InvalidTransition(ValueError):
    pass


def check_transition(current: str, target: str, actor: Literal["owner", "scan"]) -> None:
    if current not in STATUSES or target not in STATUSES:
        raise InvalidTransition(f"unknown status in {current!r} -> {target!r}")
    if actor == "owner":
        allowed = target in OWNER_TRANSITIONS[current]
    elif actor == "scan":
        allowed = target == "library" or (current == "library" and target == "review")
    else:
        raise InvalidTransition(f"unknown actor {actor!r}")
    if not allowed:
        raise InvalidTransition(f"{actor} cannot move {current} -> {target}")


def not_a_match_target(previous_status: str | None) -> str:
    if previous_status in STATUSES and previous_status != "library":
        return previous_status
    return "review"


def apply_owner_transition(store, item_id: int, target: str, now: datetime) -> Item:
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(item_id)
    check_transition(item.status, target, "owner")
    return store.set_status(item_id, target, now)


def apply_not_a_match(store, item_id: int, now: datetime) -> Item:
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(item_id)
    if item.status != "library":
        raise InvalidTransition(f"not a match requires library status, got {item.status}")
    if item.plex_rating_key:
        store.reject_rating_key(item_id, item.plex_rating_key)
    store.clear_match(item_id)
    return store.set_status(item_id, not_a_match_target(item.previous_status), now, note="not a match")
