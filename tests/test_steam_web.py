"""Behavior of the runtime Steam Web API client (profile lookups)."""

from __future__ import annotations

import json

import httpx

from tf2_loadout.steam_web import SteamWebClient

STEAM_ID = "76561197960287930"


def _client(handler) -> SteamWebClient:
    return SteamWebClient(
        "dummy-key", transport=httpx.MockTransport(handler), timeout=5.0
    )


async def test_fetch_profile_returns_persona_avatar_profile_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "response": {
                        "players": [
                            {
                                "personaname": "Spy Enjoyer",
                                "avatarfull": "https://example/avatar.jpg",
                                "profileurl": "https://steamcommunity.com/id/spy",
                            }
                        ]
                    }
                }
            ),
        )

    profile = await _client(handler).fetch_profile(STEAM_ID)
    assert profile == {
        "persona": "Spy Enjoyer",
        "avatar": "https://example/avatar.jpg",
        "profile_url": "https://steamcommunity.com/id/spy",
    }


async def test_fetch_profile_returns_none_when_no_player_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"response": {"players": []}}))

    assert await _client(handler).fetch_profile(STEAM_ID) is None


async def test_fetch_profile_swallows_failures_and_returns_none():
    """Sign-in only needs the SteamID; a profile-fetch outage must not block it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    assert await _client(handler).fetch_profile(STEAM_ID) is None
