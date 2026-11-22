"""Live verification of backpack.tf pricing.

Skipped unless ``--live`` is passed (needs a legacy BACKPACK_TF_API_KEY). Verifies the
real IGetPrices/v4 shape parses. It no longer writes the cache: ``tf2-loadout-refresh``
does that.
"""

import pytest

from tf2_loadout.pricing_client import PricingClient
from tf2_loadout.pricing import PricingService


@pytest.mark.live
async def test_real_prices_fetch_and_parse():
    async with PricingClient() as client:
        response = await client.fetch_prices()

    prices = PricingService.from_response(response)

    assert len(prices) > 1000  # thousands of priced items
    # Team Captain (defindex 378) is a long-standing, always-priced cosmetic.
    tc = prices.get(378)
    assert tc is not None
    assert tc.value > 0
    assert tc.currency in {"metal", "keys"}
