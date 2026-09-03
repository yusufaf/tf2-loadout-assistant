"""Orchestrates Steam sign-in: OpenID verification plus the best-effort profile
fetch, behind one service so ``api.py`` needs to know neither protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .steam_auth import SteamAuthError, SteamOpenID
from .steam_web import SteamWebClient


@dataclass(frozen=True)
class SignedInUser:
    steam_id: str
    persona: str | None
    avatar: str | None
    profile_url: str | None


class SteamAuthService:
    def __init__(
        self,
        *,
        public_base_url: str,
        steam_api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        return_to = f"{public_base_url}/auth/steam/return"
        self._openid = SteamOpenID(return_to=return_to, realm=public_base_url, transport=transport)
        self._web = (
            SteamWebClient(steam_api_key, transport=transport) if steam_api_key else None
        )

    def login_url(self, state: str) -> str:
        return self._openid.login_url(state)

    async def complete_login(self, params: dict[str, str]) -> SignedInUser:
        """Verify a Steam return callback, raising ``SteamAuthError`` on failure."""
        steam_id = await self._openid.verify(params)
        profile = await self._web.fetch_profile(steam_id) if self._web else None
        return SignedInUser(
            steam_id=steam_id,
            persona=(profile or {}).get("persona"),
            avatar=(profile or {}).get("avatar"),
            profile_url=(profile or {}).get("profile_url"),
        )


__all__ = ["SteamAuthService", "SignedInUser", "SteamAuthError"]
