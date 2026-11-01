from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.models import FetchLog, ScanLog
from app.plex_community import resolve_authors
from app.plex_watchlist import run_watchlist_sync
from app.poller import run_fetch
from app.scanner import run_scan

log = logging.getLogger(__name__)

DEFAULT_FEED_INTERVAL_MIN = 15
DEFAULT_SCAN_INTERVAL_MIN = 30


class Scheduler:
    def __init__(self, store, fetcher, auth, clock: Callable[[], datetime], tick_seconds: float = 5.0):
        self.store, self.fetcher, self.auth, self.clock = store, fetcher, auth, clock
        self.tick_seconds = tick_seconds
        self._fetch_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def fetch_now(self) -> FetchLog | None:
        """Fetch the feed; None if a fetch is already running. Scans afterwards when items are new."""
        if self._fetch_lock.locked():
            return None
        async with self._fetch_lock:
            result = await asyncio.to_thread(run_fetch, self.store, self.fetcher, self.clock())
            watchlist = await self._sync_watchlist()
        if result.new_count + (watchlist.new_count if watchlist else 0) > 0:
            await self.resolve_now()
            await self.scan_now()
        return result

    async def _sync_watchlist(self):
        """Sync the owner's watchlist; never raises, so it cannot break a fetch."""
        try:
            return await asyncio.to_thread(run_watchlist_sync, self.store, self.auth, self.clock())
        except Exception:
            log.warning("watchlist sync failed", exc_info=False)
            return None

    async def watchlist_now(self) -> FetchLog | None:
        """Sync the owner's watchlist on demand; None if a fetch or sync is already running."""
        if self._fetch_lock.locked():
            return None
        async with self._fetch_lock:
            result = await self._sync_watchlist()
        if result and result.new_count > 0:
            await self.resolve_now()
            await self.scan_now()
        return result

    async def resolve_now(self) -> int:
        """Resolve friend identities; never raises, so it cannot break a fetch."""
        try:
            return await asyncio.to_thread(resolve_authors, self.store, self.auth, self.clock())
        except Exception:
            log.warning("friend identity resolution failed", exc_info=False)
            return 0

    async def scan_now(self) -> ScanLog | None:
        """Scan Plex; None if a scan is already running."""
        if self._scan_lock.locked():
            return None
        async with self._scan_lock:
            return await asyncio.to_thread(run_scan, self.store, self.auth, self.clock())

    def stop(self) -> None:
        self._stop.set()

    def _interval(self, key: str, default: int) -> timedelta:
        try:
            minutes = float(self.store.get_setting(key, default))
        except (TypeError, ValueError):
            minutes = default
        return timedelta(minutes=max(minutes, 1 / 60))

    async def _safely(self, job) -> None:
        try:
            await job()
        except Exception:
            log.exception("scheduled job failed")

    async def run_forever(self) -> None:
        last_fetch = last_scan = None
        first = True
        while not self._stop.is_set():
            now = self.clock()
            if last_fetch is None or now - last_fetch >= self._interval(
                "feed_interval_min", DEFAULT_FEED_INTERVAL_MIN
            ):
                last_fetch = now
                await self._safely(self.fetch_now)
                if first:
                    first = False
                    await self.resolve_now()
            now = self.clock()
            if last_scan is None or now - last_scan >= self._interval(
                "scan_interval_min", DEFAULT_SCAN_INTERVAL_MIN
            ):
                last_scan = now
                await self._safely(self.scan_now)
            try:
                await asyncio.wait_for(self._stop.wait(), self.tick_seconds)
            except asyncio.TimeoutError:
                pass
