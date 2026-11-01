import asyncio

from app.poller import FetchResponse
from app.scheduler import Scheduler


async def test_fetch_now_then_scan_when_new(store, fetcher, connected_auth, fixture_bytes, now):
    store.set_setting("feed_url", "u")
    fetcher.queue(FetchResponse(200, fixture_bytes, None))
    s = Scheduler(store, fetcher, connected_auth, lambda: now)
    log = await s.fetch_now()
    assert log.new_count == 5 and store.last_scan() is not None


async def test_fetch_without_new_items_does_not_scan(store, fetcher, connected_auth, now):
    s = Scheduler(store, fetcher, connected_auth, lambda: now)
    log = await s.fetch_now()
    assert log.ok is False and store.last_scan() is None


async def test_overlapping_scan_is_skipped(store, slow_auth, fetcher, now):
    s = Scheduler(store, fetcher, slow_auth, lambda: now)
    a, b = await asyncio.gather(s.scan_now(), s.scan_now())
    assert slow_auth.calls == 1 and (a is None) != (b is None)


async def test_overlapping_fetch_is_skipped(store, fetcher, connected_auth, now, monkeypatch):
    import time

    import app.scheduler as sched
    from app.models import FetchLog

    calls = []

    def slow_fetch(store_, fetcher_, now_):
        calls.append(1)
        time.sleep(0.2)
        return FetchLog(now_, now_, ok=False, error="x")

    monkeypatch.setattr(sched, "run_fetch", slow_fetch)
    s = Scheduler(store, fetcher, connected_auth, lambda: now)
    a, b = await asyncio.gather(s.fetch_now(), s.fetch_now())
    assert len(calls) == 1 and (a is None) != (b is None)


async def test_run_forever_runs_both_immediately_and_stops(store, fetcher, connected_auth, clock):
    s = Scheduler(store, fetcher, connected_auth, clock, tick_seconds=0.01)
    task = asyncio.create_task(s.run_forever())
    for _ in range(100):
        if store.last_fetch() and store.last_scan():
            break
        await asyncio.sleep(0.02)
    s.stop()
    await asyncio.wait_for(task, 2)
    assert store.last_fetch() is not None and store.last_scan() is not None


async def test_interval_read_each_tick(store, fetcher, connected_auth, clock):
    s = Scheduler(store, fetcher, connected_auth, clock, tick_seconds=0.01)
    store.set_setting("scan_interval_min", 10)
    task = asyncio.create_task(s.run_forever())
    await asyncio.sleep(0.2)
    first = store.last_scan().id
    clock.advance(minutes=5)
    await asyncio.sleep(0.1)
    assert store.last_scan().id == first
    store.set_setting("scan_interval_min", 1)
    await asyncio.sleep(0.2)
    s.stop()
    await asyncio.wait_for(task, 2)
    assert store.last_scan().id > first
