from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/api/status")
def status(request: Request):
    store, auth = request.app.state.store, request.app.state.auth
    return jsonable_encoder(
        {
            "counts": store.counts_by_status(),
            "last_fetch": store.last_fetch(),
            "last_scan": store.last_scan(),
            "plex": auth.status(),
        }
    )


@router.post("/api/feed/fetch")
async def fetch_now(request: Request):
    result = await request.app.state.scheduler.fetch_now()
    return jsonable_encoder(result) if result else {"skipped": True}


@router.post("/api/plex/scan")
async def scan_now(request: Request):
    result = await request.app.state.scheduler.scan_now()
    return jsonable_encoder(result) if result else {"skipped": True}
