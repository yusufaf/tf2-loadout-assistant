"""Async HTTP client for the Steam TF2 item schema (IEconItems_440/GetSchemaItems).

The endpoint is paginated: each response returns a page of items plus a ``next`` start
index when more remain. ``fetch_all_items`` walks every page.
"""

from __future__ import annotations

import os
import re

import httpx
import vdf

from . import __version__

SCHEMA_URL = "https://api.steampowered.com/IEconItems_440/GetSchemaItems/v1/"
OVERVIEW_URL = "https://api.steampowered.com/IEconItems_440/GetSchemaOverview/v1/"
USER_AGENT = f"tf2-loadout-assistant/{__version__}"
DEFAULT_TIMEOUT = 60.0


class SchemaError(RuntimeError):
    """Raised when the schema API returns a non-OK status or a body that won't parse."""


# GetSchemaOverview hands out a content-addressed URL: the file name embeds the SHA-1
# of items_game.txt (".../items_game.<40 hex>.txt"), so a changed hash means Valve
# shipped a new items_game. Anything else is an unexpected shape: report no hash.
_ITEMS_GAME_HASH = re.compile(r"items_game\.([0-9a-f]{8,})\.txt$")


def items_game_hash(url: str) -> str | None:
    match = _ITEMS_GAME_HASH.search(url)
    return match.group(1) if match else None


def _json(resp: httpx.Response, what: str) -> dict:
    try:
        return resp.json()
    except ValueError as e:
        raise SchemaError(f"{what} returned a non-JSON body") from e


class SchemaClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = SCHEMA_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("STEAM_API_KEY", "")
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SchemaClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch_all_items(self) -> list[dict]:
        """Fetch every schema item, following pagination to the end."""
        items: list[dict] = []
        start: int | None = None
        while True:
            params: dict[str, object] = {"key": self._api_key, "language": "en"}
            if start is not None:
                params["start"] = start
            resp = await self._client.get(self._base_url, params=params)
            resp.raise_for_status()
            result = _json(resp, "GetSchemaItems").get("result", {})
            if result.get("status") != 1:
                raise SchemaError(f"schema returned status {result.get('status')!r}")
            items.extend(result.get("items", []))
            start = result.get("next")
            if start is None:
                return items

    async def fetch_items_game_url(self) -> str:
        """GetSchemaOverview's ``items_game_url``: where the current items_game.txt lives."""
        resp = await self._client.get(
            OVERVIEW_URL, params={"key": self._api_key, "language": "en"}
        )
        resp.raise_for_status()
        result = _json(resp, "GetSchemaOverview").get("result", {})
        if result.get("status") != 1:
            raise SchemaError(f"schema overview returned status {result.get('status')!r}")
        url = result.get("items_game_url")
        if not url:
            raise SchemaError("GetSchemaOverview did not return items_game_url")
        return url

    async def fetch_items_game(self, url: str | None = None) -> dict:
        """Fetch and parse items_game.txt (VDF), returning its ``items_game`` mapping.

        equip-region data (per item + the conflict matrix) lives only in this file,
        reachable via GetSchemaOverview's ``items_game_url``.
        """
        url = url or await self.fetch_items_game_url()
        resp = await self._client.get(url)
        resp.raise_for_status()
        # vdf parses an HTML error page into a junk dict without raising, so the
        # missing items_game block is what catches a bad download.
        try:
            parsed = vdf.loads(resp.text)
        except SyntaxError as e:
            raise SchemaError("items_game.txt did not parse as VDF") from e
        game = parsed.get("items_game")
        if not isinstance(game, dict) or not game:
            raise SchemaError("items_game.txt has no items_game block")
        return game
