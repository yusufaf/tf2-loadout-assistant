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
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .catalog import CatalogService
from .steam_web import SteamWebClient

# Long enough that flipping the "owned only" filter a few times in a sitting doesn't
# re-hit the API, short enough that a backpack change (a trade, an unbox) shows up
# within one sitting without an explicit refresh.
DEFAULT_TTL_SECONDS = 600.0

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
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._defindex_names = defindex_names
        self._ttl = ttl_seconds
        self._cache: dict[str, InventoryResult] = {}
        # Built once from the cosmetics catalog. reversed() so, when more than one
        # defindex shares a name, the first one in catalog order wins consistently
        # rather than depending on dict-insertion order.
        self._name_to_defindex: dict[str, int] = {
            c.name: c.defindex for c in reversed(catalog.all())
        }

    async def fetch(self, steam_id: str, *, refresh: bool = False) -> InventoryResult:
        cached = self._cache.get(steam_id)
        if cached and not refresh and (time.time() - cached.fetched_at) < self._ttl:
            return cached
        raw = await self._client.fetch_backpack(steam_id)
        result = self._resolve(raw)
        self._cache[steam_id] = result
        return result

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
