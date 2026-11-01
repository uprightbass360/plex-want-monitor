import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

from app.plex_client import (
    Pin,
    PlexError,
    PlexResource,
    PlexServerClient,
    PlexTV,
    pick_connection,
)
from app.plex_community import PlexCommunity


@dataclass(frozen=True)
class PinStatus:
    state: Literal["none", "waiting", "claimed", "expired"]
    code: str | None


@dataclass(frozen=True)
class ServerInfo:
    name: str
    machine_id: str
    base_url: str
    version: str


class PlexAuth:
    """Persists Plex sign-in state in a 0600 JSON file. Tokens never leave this file/class."""

    def __init__(
        self,
        path: Path,
        plextv: PlexTV,
        server_factory: Callable[[str, str], PlexServerClient],
        http: httpx.Client,
        clock: Callable[[], datetime],
        community_factory: Callable[[str, str], PlexCommunity] | None = None,
    ):
        self.path = Path(path)
        self.plextv = plextv
        self.server_factory = server_factory
        self.http = http
        self.clock = clock
        self.community_factory = community_factory or (lambda token, client_id: PlexCommunity(http, token, client_id))
        self._state = self._load()

    @property
    def client_id(self) -> str:
        return self._state["client_id"]

    def _load(self) -> dict:
        state = {}
        try:
            loaded = json.loads(self.path.read_text())
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            pass
        fresh = not state.get("client_id")
        state.setdefault("token", None)
        state.setdefault("username", None)
        state.setdefault("pending_pin", None)
        state.setdefault("server", None)
        if fresh:
            state["client_id"] = str(uuid.uuid4())
        self._state = state
        if fresh:
            self._save()
        return state

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def start_pin(self) -> Pin:
        pin = self.plextv.create_pin()
        self._state["pending_pin"] = {
            "id": pin.id,
            "code": pin.code,
            "expires_at": pin.expires_at.isoformat(),
        }
        self._save()
        return pin

    def poll_pin(self) -> PinStatus:
        pending = self._state["pending_pin"]
        if not pending:
            return PinStatus("none", None)
        code = pending["code"]
        if self.clock() >= datetime.fromisoformat(pending["expires_at"]):
            self._state["pending_pin"] = None
            self._save()
            return PinStatus("expired", code)
        try:
            token = self.plextv.check_pin(pending["id"])
            if not token:
                return PinStatus("waiting", code)
            username = self.plextv.user(token).username
        except PlexError:
            return PinStatus("waiting", code)  # transient: keep the PIN
        self._state.update(token=token, username=username, pending_pin=None)
        self._save()
        return PinStatus("claimed", code)

    def _token(self) -> str:
        token = self._state["token"]
        if not token:
            raise PlexError("not signed in to Plex")
        return token

    def list_servers(self) -> list[PlexResource]:
        return self.plextv.servers(self._token())

    def _resolve(self, machine_id: str) -> tuple[PlexResource, str]:
        resource = next((r for r in self.list_servers() if r.machine_id == machine_id), None)
        if resource is None:
            raise PlexError("server not found on this Plex account")
        server_token = resource.access_token or self._token()
        url = pick_connection(resource, server_token, self.http)
        if url is None:
            raise PlexError("no reachable connection")
        return resource, url

    def select_server(self, machine_id: str) -> ServerInfo:
        resource, url = self._resolve(machine_id)
        self._state["server"] = {
            "name": resource.name,
            "machine_id": resource.machine_id,
            "base_url": url,
            "access_token": resource.access_token or self._token(),
            "version": resource.version,
        }
        self._save()
        return ServerInfo(resource.name, resource.machine_id, url, resource.version)

    def reconnect(self) -> ServerInfo:
        server = self._state["server"]
        if not server:
            raise PlexError("no server selected")
        resource, url = self._resolve(server["machine_id"])
        server.update(
            name=resource.name,
            base_url=url,
            version=resource.version,
            access_token=resource.access_token or self._token(),
        )
        self._save()
        return ServerInfo(server["name"], server["machine_id"], url, server["version"])

    def signout(self) -> None:
        self._state.update(token=None, username=None, pending_pin=None, server=None)
        self._save()

    def server_client(self) -> PlexServerClient | None:
        s = self._state["server"]
        if not s:
            return None
        return self.server_factory(s["base_url"], s["access_token"])

    def community_client(self) -> PlexCommunity | None:
        token = self._state["token"]
        if not token:
            return None
        return self.community_factory(token, self.client_id)

    def status(self) -> dict:
        s = self._state["server"]
        p = self._state["pending_pin"]
        return {
            "signed_in": bool(self._state["token"]),
            "username": self._state["username"],
            "server": {
                "name": s["name"],
                "machine_id": s["machine_id"],
                "version": s["version"],
                "base_url_host": urlparse(s["base_url"]).hostname,
            }
            if s
            else None,
            "pending_pin": {"code": p["code"], "expires_at": p["expires_at"]} if p else None,
        }
