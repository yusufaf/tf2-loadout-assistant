"""Live end-to-end run of tf2-loadout-refresh against Steam and backpack.tf.

Skipped unless ``--live`` is passed (needs STEAM_API_KEY and BACKPACK_TF_API_KEY).
Writes into a temporary directory, never the project's ``.cache/``.
"""

from __future__ import annotations

import re

import pytest

from tf2_loadout.catalog import CatalogService
from tf2_loadout.pricing import PricingService
from tf2_loadout.pricing_client import PricingClient
from tf2_loadout.refresh import refresh
from tf2_loadout.schema_client import SchemaClient


@pytest.mark.live
async def test_refresh_against_real_apis(tmp_path):
    async with SchemaClient() as schema_client, PricingClient() as pricing_client:
        report = await refresh(tmp_path, schema_client, pricing_client)

    assert re.fullmatch(r"[0-9a-f]{40}", report.items_game_hash)
    assert report.catalog_rebuilt
    assert len(CatalogService.from_cache(tmp_path)) == report.cosmetics
    assert len(PricingService.from_cache(tmp_path)) == report.priced
