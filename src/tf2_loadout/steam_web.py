"""Runtime Steam Web API client (profile lookups, later the TF2 backpack).

Distinct from ``schema_client.py``, which is a dev/live-test-only tool for
rebuilding the on-disk catalog cache. This one is called on every sign-in.
"""

from __future__ import annotations

import httpx

from . import __version__

PLAYER_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
USER_AGENT = f"tf2-loadout-assistant/{__version__}"
DEFAULT_TIMEOUT = 15.0


class SteamWebClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = PLAYER_SUMMARIES_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SteamWebClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch_profile(self, steam_id: str) -> dict | None:
        """Fetch persona/avatar/profile URL for a signed-in user.

        Best-effort: sign-in only needs the verified SteamID, so a schema change or
        an outage here should never block it. Same defensive posture as
        ``lore.py``'s ``get_lore`` -- swallow and return None.
        """
        try:
            resp = await self._client.get(
                self._base_url,
                params={"key": self._api_key, "steamids": steam_id},
            )
            resp.raise_for_status()
            players = resp.json().get("response", {}).get("players", [])
        except Exception:
            return None
        if not players:
            return None
        player = players[0]
        return {
            "persona": player.get("personaname"),
            "avatar": player.get("avatarfull"),
            "profile_url": player.get("profileurl"),
        }
