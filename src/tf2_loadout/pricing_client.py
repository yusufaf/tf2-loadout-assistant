"""Async HTTP client for backpack.tf IGetPrices/v4 (requires a legacy API key)."""

from __future__ import annotations

import os

import httpx

from . import __version__

PRICES_URL = "https://backpack.tf/api/IGetPrices/v4/"
USER_AGENT = f"tf2-loadout-assistant/{__version__}"
DEFAULT_TIMEOUT = 60.0


class PricingError(RuntimeError):
    """Raised when backpack.tf returns an unsuccessful response."""


class PricingClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = PRICES_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("BACKPACK_TF_API_KEY", "")
        )
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PricingClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch_prices(self) -> dict:
        """Fetch the full price index. Returns the raw IGetPrices/v4 JSON."""
        resp = await self._client.get(
            self._base_url, params={"key": self._api_key, "appid": 440}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("response", {}).get("success") != 1:
            message = data.get("response", {}).get("message", "unknown error")
            raise PricingError(f"backpack.tf: {message}")
        return data
