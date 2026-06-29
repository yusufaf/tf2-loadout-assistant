"""Live end-to-end build of the cosmetic catalog from real Steam data.

Skipped unless ``--live`` is passed (needs STEAM_API_KEY in the environment). On success
it writes the catalog cache so the app can run offline afterwards.
"""

from pathlib import Path

import pytest

from tf2_loadout.schema_client import SchemaClient
from tf2_loadout.catalog import CatalogService, save_catalog_cache

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"


@pytest.mark.live
async def test_builds_real_catalog_and_caches():
    async with SchemaClient() as client:
        schema_items = await client.fetch_all_items()
        items_game = await client.fetch_items_game()

    catalog = CatalogService.build_from_sources(schema_items, items_game)

    # Thousands of real cosmetics, each with at least one equip region.
    assert len(catalog) > 500
    spy_hats = catalog.for_class("Spy")
    assert len(spy_hats) > 50
    assert all(c.equip_regions for c in spy_hats)

    # Two same-class hats must be flagged as conflicting (both occupy "hat").
    hats = [c for c in spy_hats if "hat" in c.equip_regions][:2]
    assert len(catalog.conflicts(hats)) == 1

    save_catalog_cache(schema_items, items_game, CACHE_DIR)
    assert (CACHE_DIR / "schema_items.json").exists()
    assert (CACHE_DIR / "equip.json").exists()
