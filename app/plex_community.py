"""Friend identity (username, display name, avatar) from the Plex community GraphQL API."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.plex_client import PLEX_HEADERS, PlexError

log = logging.getLogger(__name__)

COMMUNITY_URL = "https://community.plex.tv/api"
COMMUNITY_TIMEOUT = 10.0
_QUERY = "query($id: ID!) { user(id: $id) { id username displayName avatar } }"


@dataclass(frozen=True)
class CommunityUser:
    id: str
    username: str
    display_name: str
    avatar_url: str | None


class PlexCommunity:
    def __init__(self, http: httpx.Client, token: str, client_id: str):
        self.http = http
        self.token = token
        self.client_id = client_id

    def user(self, user_id: str) -> CommunityUser | None:
        headers = {
            **PLEX_HEADERS,
            "X-Plex-Client-Identifier": self.client_id,
            "X-Plex-Token": self.token,
            "Content-Type": "application/json",
        }
        try:
            r = self.http.post(
                COMMUNITY_URL,
                headers=headers,
                json={"query": _QUERY, "variables": {"id": user_id}},
                timeout=COMMUNITY_TIMEOUT,
            )
        except httpx.HTTPError as e:  # never echo e: it may carry request details
            raise PlexError(f"could not reach community.plex.tv: {type(e).__name__}") from None
        if r.status_code != 200:
            raise PlexError(f"community.plex.tv answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise PlexError("community.plex.tv returned invalid JSON") from None
        if not isinstance(body, dict):
            raise PlexError("community.plex.tv returned an unexpected response")
        if body.get("errors"):
            raise PlexError("community.plex.tv reported a GraphQL error")
        data = body.get("data")
        u = data.get("user") if isinstance(data, dict) else None
        if not isinstance(u, dict):
            return None
        return CommunityUser(
            id=str(u.get("id") or user_id),
            username=u.get("username") or "",
            display_name=u.get("displayName") or "",
            avatar_url=u.get("avatar") or None,
        )


def resolve_authors(store, auth, now: datetime) -> int:
    """Resolve unresolved friends. Stops at the first PlexError; those stay unresolved for retry."""
    client = auth.community_client()
    if client is None:
        return 0
    count = 0
    for h in store.unresolved_authors():
        try:
            u = client.user(h)
        except PlexError as exc:
            log.warning("friend identity lookup failed: %s", exc)
            break
        if u is None:
            store.set_author_identity(h, "", "", None, now)
        else:
            store.set_author_identity(h, u.username, u.display_name, u.avatar_url, now)
        count += 1
    return count
