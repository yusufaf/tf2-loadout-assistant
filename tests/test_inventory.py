"""Resolving a raw backpack into the catalog's own defindex space.

The Steam client is stubbed here -- ``test_steam_web.py`` covers the HTTP shape.
This file is about status mapping, name-fallback resolution, and the TTL cache.
"""

from __future__ import annotations

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
