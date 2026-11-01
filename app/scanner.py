from datetime import datetime

from app.lifecycle import check_transition
from app.matcher import build_index, find_match
from app.models import ItemFilter, ScanLog
from app.plex_auth import PlexAuth
from app.plex_client import PlexError
from app.store import StaleStatus, Store


def run_scan(store: Store, auth: PlexAuth, now: datetime) -> ScanLog:
    log = _scan(store, auth, now)
    log.id = store.log_scan(log)
    return log


def _scan(store: Store, auth: PlexAuth, now: datetime) -> ScanLog:
    client = auth.server_client()
    if client is None:
        return ScanLog(now, now, False, error="Plex not connected")
    try:
        machine_id = client.identity().machine_id
        sections = client.sections()
    except PlexError as e:
        return ScanLog(now, now, False, error=str(e))

    selected = store.get_setting("selected_library_keys")
    # Unselected libraries are recorded (not listed) so Settings can offer every library
    # from the scan log without calling Plex.
    unselected = [
        {"key": s.key, "name": s.title, "ok": None, "count": 0, "error": None, "selected": False}
        for s in sections if selected and s.key not in selected
    ]
    if selected:
        sections = [s for s in sections if s.key in selected]

    # revert_keys: libraries whose listing can confirm a removal. An empty listing cannot
    # (a library that suddenly lists nothing is more likely a Plex hiccup than a wipe).
    listings, libraries, listed_keys, revert_keys = [], [], set(), set()
    for lib in sections:
        try:
            entries = client.list_all(lib.key)
        except PlexError as e:
            libraries.append({"key": lib.key, "name": lib.title, "ok": False, "count": 0, "error": str(e)})
            continue
        listings.append((lib, entries))
        listed_keys.add(lib.key)
        if entries:
            revert_keys.add(lib.key)
        libraries.append({"key": lib.key, "name": lib.title, "ok": True, "count": len(entries), "error": None})

    index = build_index(listings)
    matched = reverted = 0
    for item in store.list_items(ItemFilter()):
        if item.status != "library":
            m = find_match(item.type, item.guid, item.title, item.year, index, item.rejected_rating_keys)
            if m:
                check_transition(item.status, "library", "scan")
                try:
                    store.set_status(item.id, "library", now, expected_from=item.status)
                except StaleStatus:
                    continue  # changed since we read it; the next scan sees the new status
                store.set_match(item.id, m.rating_key, m.library_key, m.library_name, machine_id, m.method, now)
                matched += 1
        elif (
            item.plex_machine_id == machine_id
            and item.plex_library_key in revert_keys
            and item.plex_rating_key not in index.rating_keys
        ):
            check_transition("library", "review", "scan")
            try:
                store.set_status(item.id, "review", now, note="removed from Plex", expected_from="library")
            except StaleStatus:
                continue
            store.clear_match(item.id)
            reverted += 1

    return ScanLog(
        now, now, bool(listed_keys),
        error=None if listed_keys else "no library could be listed",
        machine_id=machine_id, libraries=libraries + unselected,
        indexed=len(index.rating_keys), matched=matched, reverted=reverted,
    )
