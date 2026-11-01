import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

PLEX_HEADERS = {
    "X-Plex-Product": "Want Monitor",
    "X-Plex-Version": "1.0",
    "X-Plex-Device": "Docker",
    "X-Plex-Platform": "Linux",
    "Accept": "application/json",
}
PLEXTV = "https://plex.tv"
PLEXTV_TIMEOUT = 10.0


class PlexError(Exception):
    """Failure talking to Plex. The message is safe to display: no token, no query string."""


@dataclass(frozen=True)
class Pin:
    id: int
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class PlexUser:
    id: int
    username: str
    uuid: str | None = None


@dataclass(frozen=True)
class Connection:
    uri: str
    local: bool
    relay: bool


@dataclass(frozen=True)
class PlexResource:
    name: str
    machine_id: str
    owned: bool
    access_token: str
    version: str
    connections: tuple[Connection, ...]


@dataclass(frozen=True)
class ServerIdentity:
    machine_id: str
    version: str


@dataclass(frozen=True)
class Library:
    key: str
    type: str
    title: str


@dataclass(frozen=True)
class PlexEntry:
    rating_key: str
    title: str
    year: int | None
    type: str
    guids: tuple[str, ...]
    plex_guid: str | None = None


def _get_json(http: httpx.Client, method: str, url: str, headers: dict, timeout: float):
    host = httpx.URL(url).host
    try:
        r = http.request(method, url, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:  # never echo e: it may carry the URL
        raise PlexError(f"could not reach {host}: {type(e).__name__}") from None
    if r.status_code not in (200, 201):  # plex.tv answers 201 to POST /pins
        raise PlexError(f"{host} answered HTTP {r.status_code}")
    try:
        return json.loads(r.content.decode("utf-8", errors="replace"))
    except ValueError:
        raise PlexError(f"{host} returned invalid JSON") from None


def _parse_time(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class PlexTV:
    def __init__(self, client_id: str, http: httpx.Client):
        self.client_id = client_id
        self.http = http

    def _headers(self, token: str | None = None) -> dict:
        h = {**PLEX_HEADERS, "X-Plex-Client-Identifier": self.client_id}
        if token:
            h["X-Plex-Token"] = token
        return h

    def _call(self, method: str, path: str, token: str | None = None):
        return _get_json(self.http, method, f"{PLEXTV}{path}", self._headers(token), PLEXTV_TIMEOUT)

    def create_pin(self) -> Pin:
        d = self._call("POST", "/api/v2/pins")
        try:
            return Pin(int(d["id"]), d["code"], _parse_time(d["expiresAt"]))
        except (KeyError, TypeError, ValueError):
            raise PlexError("plex.tv returned an unexpected PIN response") from None

    def check_pin(self, pin_id: int) -> str | None:
        d = self._call("GET", f"/api/v2/pins/{pin_id}")
        return d.get("authToken") or None

    def user(self, token: str) -> PlexUser:
        d = self._call("GET", "/api/v2/user", token)
        try:
            return PlexUser(int(d["id"]), d["username"], str(d["uuid"]) if d.get("uuid") else None)
        except (KeyError, TypeError, ValueError):
            raise PlexError("plex.tv returned an unexpected user response") from None

    def servers(self, token: str) -> list[PlexResource]:
        data = self._call("GET", "/api/v2/resources?includeHttps=1&includeRelay=0", token)
        out = []
        try:
            for r in data:
                if "server" not in (r.get("provides") or ""):
                    continue
                conns = tuple(
                    Connection(c["uri"], bool(c.get("local")), bool(c.get("relay")))
                    for c in r.get("connections") or []
                )
                out.append(
                    PlexResource(
                        name=r["name"],
                        machine_id=r["clientIdentifier"],
                        owned=bool(r.get("owned")),
                        access_token=r.get("accessToken") or "",
                        version=r.get("productVersion") or "",
                        connections=conns,
                    )
                )
        except (KeyError, TypeError, AttributeError):
            raise PlexError("plex.tv returned an unexpected resources response") from None
        return out


class PlexServerClient:
    """Read-only client: only GET requests are ever sent to the Plex Media Server."""

    def __init__(self, base_url: str, token: str, http: httpx.Client, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        headers = {**PLEX_HEADERS, "X-Plex-Token": self.token}
        d = _get_json(self.http, "GET", f"{self.base_url}{path}", headers, self.timeout)
        if not isinstance(d, dict) or not isinstance(d.get("MediaContainer"), dict):
            raise PlexError(f"{httpx.URL(self.base_url).host} returned an unexpected response")
        return d["MediaContainer"]

    def identity(self) -> ServerIdentity:
        mc = self._get("/identity")
        try:
            return ServerIdentity(mc["machineIdentifier"], mc["version"])
        except KeyError:
            raise PlexError("server identity response is incomplete") from None

    def sections(self) -> list[Library]:
        mc = self._get("/library/sections")
        try:
            return [Library(str(d["key"]), d["type"], d["title"]) for d in mc.get("Directory", [])]
        except KeyError:
            raise PlexError("library section response is incomplete") from None

    def list_all(self, section_key: str, page_size: int = 200) -> list[PlexEntry]:
        out: list[PlexEntry] = []
        start = 0
        total: int | None = None
        max_pages = 1000  # replaced by totalSize // page_size + 2 once totalSize is known
        pages = 0
        while True:
            mc = self._get(
                f"/library/sections/{section_key}/all?includeGuids=1"
                f"&X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}"
            )
            pages += 1
            try:
                page = mc.get("Metadata") or []
                out.extend(
                    PlexEntry(
                        rating_key=str(m["ratingKey"]),
                        title=m["title"],
                        year=m.get("year"),
                        type=m["type"],
                        guids=tuple(g["id"] for g in m.get("Guid") or []),
                        plex_guid=m.get("guid") or None,
                    )
                    for m in page
                )
                if mc.get("totalSize") is not None:
                    total = int(mc["totalSize"])
            except (KeyError, TypeError, ValueError, AttributeError):
                raise PlexError("library listing response is incomplete") from None
            start += len(page)
            if not page:
                return out
            if total is None:
                if len(page) < page_size:
                    return out
            else:
                max_pages = total // page_size + 2
                if start >= total:
                    return out
            if pages >= max_pages:
                raise PlexError("library listing is too long to read completely")


def pick_connection(
    resource: PlexResource, token: str, http: httpx.Client, timeout: float = 5
) -> str | None:
    conns = resource.connections
    ordered = [c for c in conns if c.local] + [c for c in conns if not c.local and not c.relay]
    headers = {**PLEX_HEADERS, "X-Plex-Token": token}
    for c in ordered:
        try:
            r = http.get(f"{c.uri.rstrip('/')}/identity", headers=headers, timeout=timeout)
        except Exception:
            continue
        if r.status_code == 200:
            return c.uri
    return None
