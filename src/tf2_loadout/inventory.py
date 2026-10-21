"""Resolves a Steam backpack into the catalog's own defindex space.

The backpack API returns bare defindexes and nothing else -- no names. Most of
those line up with a cosmetic's own defindex directly, but Valve sometimes tracks
the same cosmetic under more than one defindex (a Genuine/promo quality variant, a
re-release), so a miss falls back to matching by name against a full defindex/name
index built from the raw schema (``catalog.load_defindex_names``), not just the
cosmetics catalog. A backpack defindex that resolves through neither path is
dropped -- there's nothing else to match it against, the same "unknown over
guessed" rule as an unpriced item.

Results are cached briefly per SteamID: Steam has no push notification for
inventory changes, and the frontend re-checks this on every filter-panel mount, so
without a cache a fast one would round-trip the API on every glance at the tray.
Concurrent fetches for one SteamID share a single backpack call, and the cache is
capped at ``max_entries`` so a stream of sign-ins can't grow it without bound.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .catalog import CatalogService
from .steam_web import SteamWebClient

# Long enough that flipping the "owned only" filter a few times in a sitting doesn't
# re-hit the API, short enough that a backpack change (a trade, an unbox) shows up
# within one sitting without an explicit refresh.
DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_ENTRIES = 1024

# GetPlayerItems result.status codes we distinguish; anything else (including a
# transport failure, folded to 0 by SteamWebClient.fetch_backpack) reads as "error".
_STATUS_OK = 1
_STATUS_PRIVATE = 15
_STATUS_NOT_FOUND = {8, 18}


@dataclass(frozen=True)
class InventoryResult:
    status: str  # "ok" | "private" | "not_found" | "error"
    defindexes: frozenset[int]
    fetched_at: float


class InventoryService:
    def __init__(
        self,
        client: SteamWebClient,
        catalog: CatalogService,
        defindex_names: dict[int, str],
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._defindex_names = defindex_names
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: dict[str, InventoryResult] = {}
        self._inflight: dict[str, asyncio.Task[InventoryResult]] = {}
        # Built once from the cosmetics catalog. reversed() so, when more than one
        # defindex shares a name, the first one in catalog order wins consistently
        # rather than depending on dict-insertion order.
        self._name_to_defindex: dict[str, int] = {
            c.name: c.defindex for c in reversed(catalog.all())
        }

    async def fetch(self, steam_id: str, *, refresh: bool = False) -> InventoryResult:
        cached = self._cache.get(steam_id)
        if cached and not refresh and self._fresh(cached):
            return cached
        # A refresh joins an in-flight fetch too: that fetch is asking Steam right now,
        # so its answer is as fresh as a second call's would be.
        task = self._inflight.get(steam_id)
        if task is None:
            task = asyncio.create_task(self._load(steam_id))
            self._inflight[steam_id] = task
            task.add_done_callback(lambda done: self._forget(steam_id, done))
        # shield: one caller disconnecting mustn't cancel the fetch the others await.
        return await asyncio.shield(task)

    def _fresh(self, result: InventoryResult) -> bool:
        return (time.time() - result.fetched_at) < self._ttl

    def _forget(self, steam_id: str, task: asyncio.Task[InventoryResult]) -> None:
        if self._inflight.get(steam_id) is task:
            del self._inflight[steam_id]

    async def _load(self, steam_id: str) -> InventoryResult:
        raw = await self._client.fetch_backpack(steam_id)
        result = self._resolve(raw)
        # "error" means the fetch itself failed (a network blip, a Steam outage) --
        # transient, not a fact about the account like "ok"/"private"/"not_found"
        # are. Caching it would strand every caller behind a stale failure for the
        # full TTL with no way to force a retry; leaving it uncached means the very
        # next call (including the next chat turn) tries again instead of reusing
        # yesterday's bad luck.
        if result.status != "error":
            self._store(steam_id, result)
        return result

    def _store(self, steam_id: str, result: InventoryResult) -> None:
        # pop then set, so dict order is fetch order and the first entry is oldest.
        self._cache.pop(steam_id, None)
        self._cache[steam_id] = result
        if len(self._cache) <= self._max_entries:
            return
        for sid in [sid for sid, cached in self._cache.items() if not self._fresh(cached)]:
            del self._cache[sid]
        while len(self._cache) > self._max_entries:
            del self._cache[next(iter(self._cache))]

    def _resolve(self, raw: dict) -> InventoryResult:
        status_code = raw.get("status")
        now = time.time()
        if status_code == _STATUS_PRIVATE:
            return InventoryResult("private", frozenset(), now)
        if status_code in _STATUS_NOT_FOUND:
            return InventoryResult("not_found", frozenset(), now)
        if status_code != _STATUS_OK:
            return InventoryResult("error", frozenset(), now)

        owned: set[int] = set()
        for item in raw.get("items", []):
            defindex = item.get("defindex")
            if defindex is None:
                continue
            if self._catalog.get(defindex) is not None:
                owned.add(defindex)
                continue
            name = self._defindex_names.get(defindex)
            mapped = self._name_to_defindex.get(name) if name else None
            if mapped is not None:
                owned.add(mapped)
        return InventoryResult("ok", frozenset(owned), now)
