"""Resolving a raw backpack into the catalog's own defindex space.

The Steam client is stubbed here -- ``test_steam_web.py`` covers the HTTP shape.
This file is about status mapping, name-fallback resolution, the TTL cache, and
in-flight coalescing.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tf2_loadout.catalog import CatalogService
from tf2_loadout.inventory import InventoryService
from tf2_loadout.models import Cosmetic

STEAM_ID = "76561197960287930"

FEDORA = Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")
CATALOG = CatalogService([FEDORA])
DEFINDEX_NAMES = {1: "Spy Fedora"}


class _StubClient:
    def __init__(self, handler):
        self._handler = handler

    async def fetch_backpack(self, steam_id: str) -> dict:
        return self._handler(steam_id)


def _service(handler, names=DEFINDEX_NAMES, **kwargs) -> InventoryService:
    return InventoryService(_StubClient(handler), CATALOG, names, **kwargs)


async def test_direct_defindex_match():
    service = _service(lambda sid: {"status": 1, "items": [{"defindex": 1}]})
    result = await service.fetch(STEAM_ID)
    assert result.status == "ok"
    assert result.defindexes == frozenset({1})


async def test_falls_back_to_name_match_when_the_defindex_is_not_a_cosmetic():
    # 999 isn't in the catalog; its schema name matches the fedora's own name.
    names = {999: "Spy Fedora"}
    service = _service(lambda sid: {"status": 1, "items": [{"defindex": 999}]}, names)
    result = await service.fetch(STEAM_ID)
    assert result.defindexes == frozenset({1})


async def test_unmatched_defindex_is_dropped_not_guessed():
    service = _service(lambda sid: {"status": 1, "items": [{"defindex": 12345}]})
    result = await service.fetch(STEAM_ID)
    assert result.status == "ok"
    assert result.defindexes == frozenset()


async def test_private_backpack_reports_private_status():
    service = _service(lambda sid: {"status": 15})
    result = await service.fetch(STEAM_ID)
    assert result.status == "private"
    assert result.defindexes == frozenset()


@pytest.mark.parametrize("code", [8, 18])
async def test_bad_steamid_reports_not_found_status(code):
    service = _service(lambda sid: {"status": code})
    result = await service.fetch(STEAM_ID)
    assert result.status == "not_found"


async def test_unmapped_status_reports_error():
    service = _service(lambda sid: {"status": 0})
    result = await service.fetch(STEAM_ID)
    assert result.status == "error"


async def test_result_is_cached_within_the_ttl():
    calls = []

    def handler(sid):
        calls.append(sid)
        return {"status": 1, "items": [{"defindex": 1}]}

    service = _service(handler, ttl_seconds=600.0)
    await service.fetch(STEAM_ID)
    await service.fetch(STEAM_ID)
    assert len(calls) == 1


async def test_an_error_result_is_not_cached_so_the_next_call_retries():
    # A transient failure must self-heal on the very next call, not strand every
    # caller (including the next chat turn) behind a stale error for the full TTL.
    calls = []

    def handler(sid):
        calls.append(sid)
        return {"status": 1, "items": [{"defindex": 1}]} if len(calls) > 1 else {"status": 0}

    service = _service(handler, ttl_seconds=600.0)
    first = await service.fetch(STEAM_ID)
    second = await service.fetch(STEAM_ID)

    assert first.status == "error"
    assert second.status == "ok"
    assert len(calls) == 2


async def test_refresh_bypasses_the_cache():
    calls = []

    def handler(sid):
        calls.append(sid)
        return {"status": 1, "items": [{"defindex": 1}]}

    service = _service(handler, ttl_seconds=600.0)
    await service.fetch(STEAM_ID)
    await service.fetch(STEAM_ID, refresh=True)
    assert len(calls) == 2


async def test_cache_expires_after_the_ttl(monkeypatch):
    calls = []

    def handler(sid):
        calls.append(sid)
        return {"status": 1, "items": [{"defindex": 1}]}

    service = _service(handler, ttl_seconds=1.0)
    await service.fetch(STEAM_ID)

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 2.0)
    await service.fetch(STEAM_ID)
    assert len(calls) == 2


class _GatedClient:
    """Holds every backpack call open until ``gate`` is set, counting the calls."""

    def __init__(self, status: int = 1) -> None:
        self.gate = asyncio.Event()
        self.calls: list[str] = []
        self.status = status

    async def fetch_backpack(self, steam_id: str) -> dict:
        self.calls.append(steam_id)
        await self.gate.wait()
        return {"status": self.status, "items": [{"defindex": 1}]}


def _gated(status: int = 1, **kwargs) -> tuple[InventoryService, _GatedClient]:
    client = _GatedClient(status)
    return InventoryService(client, CATALOG, DEFINDEX_NAMES, **kwargs), client


async def _open(service: InventoryService, n: int, **kwargs) -> list[asyncio.Task]:
    tasks = [asyncio.create_task(service.fetch(STEAM_ID, **kwargs)) for _ in range(n)]
    await asyncio.sleep(0)
    return tasks


async def test_concurrent_fetches_share_one_backpack_call():
    service, client = _gated()
    tasks = await _open(service, 3)
    client.gate.set()
    results = await asyncio.gather(*tasks)
    assert len(client.calls) == 1
    assert results[0] == results[1] == results[2]


async def test_refresh_joins_an_inflight_fetch():
    service, client = _gated()
    tasks = await _open(service, 1)
    tasks += await _open(service, 1, refresh=True)
    client.gate.set()
    await asyncio.gather(*tasks)
    assert len(client.calls) == 1


async def test_one_cancelled_caller_does_not_cancel_the_shared_fetch():
    service, client = _gated()
    leaving, staying = await _open(service, 2)
    leaving.cancel()
    await asyncio.sleep(0)
    client.gate.set()
    assert (await staying).status == "ok"
    assert leaving.cancelled()
    assert len(client.calls) == 1


async def test_a_finished_fetch_is_not_joined_by_the_next_refresh():
    service, client = _gated()
    client.gate.set()
    await service.fetch(STEAM_ID)
    await service.fetch(STEAM_ID, refresh=True)
    assert len(client.calls) == 2


async def test_error_results_are_shared_but_not_cached():
    service, client = _gated(status=0)
    tasks = await _open(service, 2)
    client.gate.set()
    assert [r.status for r in await asyncio.gather(*tasks)] == ["error", "error"]
    assert len(client.calls) == 1
    await service.fetch(STEAM_ID)
    assert len(client.calls) == 2


def _counting(**kwargs) -> tuple[InventoryService, list[str]]:
    calls: list[str] = []

    def handler(sid):
        calls.append(sid)
        return {"status": 1, "items": [{"defindex": 1}]}

    return _service(handler, **kwargs), calls


async def test_cache_is_bounded_by_max_entries():
    service, calls = _counting(max_entries=2)
    for sid in ("a", "b", "c"):
        await service.fetch(sid)
    await service.fetch("c")
    assert calls == ["a", "b", "c"]  # c still cached
    await service.fetch("a")
    assert calls == ["a", "b", "c", "a"]  # a was the oldest, so it went


async def test_expired_entries_are_evicted_first(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    service, calls = _counting(max_entries=2, ttl_seconds=600.0)
    await service.fetch("b")
    clock[0] = 0.0
    await service.fetch("a")  # newest entry, but...
    clock[0] = 1000.0  # ...now expired, while the older b is still fresh
    await service.fetch("c")
    await service.fetch("b")
    assert calls == ["b", "a", "c"]  # b survived the eviction
    await service.fetch("a")
    assert calls == ["b", "a", "c", "a"]
