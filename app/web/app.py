from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Config
from app.db import connect, migrate
from app.plex_auth import PlexAuth
from app.plex_client import PlexServerClient, PlexTV
from app.poller import HttpxFeedFetcher
from app.scheduler import Scheduler
from app.store import Store
from app.web.api import router as api_router
from app.web.pages import router as pages_router

STATIC_DIR = Path(__file__).parent / "static"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    config: Config | None = None,
    *,
    store=None,
    auth=None,
    fetcher=None,
    scheduler_enabled: bool = True,
) -> FastAPI:
    config = config or Config.from_env({})

    closers: list = []
    if store is None:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        conn = connect(config.db_path, check_same_thread=False)
        migrate(conn)
        closers.append(conn)
        store = Store(conn)
    if fetcher is None:
        feed_http = httpx.Client(follow_redirects=True)
        closers.append(feed_http)
        fetcher = HttpxFeedFetcher(feed_http)
    if auth is None:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        http = httpx.Client(follow_redirects=True)
        closers.append(http)
        plextv = PlexTV("", http)
        auth = PlexAuth(
            config.plex_path,
            plextv,
            lambda url, token: PlexServerClient(url, token, http),
            http,
            _utcnow,
        )
        plextv.client_id = auth.client_id

    scheduler = Scheduler(store, fetcher, auth, _utcnow)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(scheduler.run_forever()) if scheduler_enabled else None
        try:
            yield
        finally:
            scheduler.stop()
            if task:
                await task
            for resource in closers:
                with contextlib.suppress(Exception):
                    resource.close()

    app = FastAPI(title="Want Monitor", lifespan=lifespan)
    app.state.config = config
    app.state.store = store
    app.state.auth = auth
    app.state.fetcher = fetcher
    app.state.scheduler = scheduler
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api_router)
    app.include_router(pages_router)
    return app
