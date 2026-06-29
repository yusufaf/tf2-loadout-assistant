"""Behavior of the backpack.tf pricing HTTP client."""

import json

import httpx
import pytest

from tf2_loadout.pricing_client import PricingClient, PricingError
from tf2_loadout.pricing import PricingService


def _response(items):
    return httpx.Response(
        200, content=json.dumps({"response": {"success": 1, "items": items}})
    )


async def test_fetch_sends_key_and_appid_and_returns_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.url.params.get("key")
        seen["appid"] = request.url.params.get("appid")
        return _response(
            {
                "Team Captain": {
                    "defindex": [378],
                    "prices": {
                        "6": {"Tradable": {"Craftable": [{"currency": "metal", "value": 1.5}]}}
                    },
                }
            }
        )

    transport = httpx.MockTransport(handler)
    async with PricingClient(api_key="legacy-key", transport=transport) as client:
        data = await client.fetch_prices()

    assert seen == {"key": "legacy-key", "appid": "440"}
    prices = PricingService.from_response(data)
    assert prices.get(378).value == 1.5


async def test_unsuccessful_response_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {"response": {"success": 0, "message": "This API key is not valid."}}
            ),
        )

    transport = httpx.MockTransport(handler)
    async with PricingClient(api_key="bad", transport=transport) as client:
        with pytest.raises(PricingError, match="not valid"):
            await client.fetch_prices()
