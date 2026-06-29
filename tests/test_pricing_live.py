"""Live verification of backpack.tf pricing.

Skipped unless ``--live`` is passed (needs a legacy BACKPACK_TF_API_KEY). Verifies the
real IGetPrices/v4 shape parses and caches prices for offline use.
"""

from pathlib import Path

import pytest

from tf2_loadout.pricing_client import PricingClient
from tf2_loadout.pricing import PricingService

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"


@pytest.mark.live
async def test_real_prices_fetch_parse_and_cache():
    async with PricingClient() as client:
        response = await client.fetch_prices()

    prices = PricingService.from_response(response)

    assert len(prices) > 1000  # thousands of priced items
    # Team Captain (defindex 378) is a long-standing, always-priced cosmetic.
    tc = prices.get(378)
    assert tc is not None
    assert tc.value > 0
    assert tc.currency in {"metal", "keys"}

    prices.save_cache(CACHE_DIR)
    assert (CACHE_DIR / "prices.json").exists()
