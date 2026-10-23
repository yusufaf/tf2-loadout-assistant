"""Behavior of the runtime Steam Web API client (profile lookups)."""

from __future__ import annotations

import json

import httpx
import pytest

from tf2_loadout.security_headers import CSP_DIRECTIVES
from tf2_loadout.steam_web import AVATAR_ORIGIN, SteamWebClient, pin_avatar_url

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


@pytest.mark.parametrize(
    "avatar",
    [
        "https://avatars.fastly.steamstatic.com/abc_full.jpg",
        "https://avatars.cloudflare.steamstatic.com/abc_full.jpg",
        "http://avatars.akamai.steamstatic.com/abc_full.jpg",
        "https://avatars.steamstatic.com/abc_full.jpg",
    ],
)
async def test_fetch_profile_pins_steam_avatars_to_one_https_host(avatar):
    def handler(request: httpx.Request) -> httpx.Response:
        players = [{"personaname": "Spy", "avatarfull": avatar}]
        return httpx.Response(200, content=json.dumps({"response": {"players": players}}))

    profile = await _client(handler).fetch_profile(STEAM_ID)
    assert profile["avatar"] == "https://avatars.steamstatic.com/abc_full.jpg"


def test_pin_avatar_url_keeps_the_rest_of_the_url_and_other_hosts():
    pinned = pin_avatar_url("http://avatars.fastly.steamstatic.com/abc_full.jpg?t=1")
    assert pinned == "https://avatars.steamstatic.com/abc_full.jpg?t=1"
    assert pin_avatar_url("https://example/avatar.jpg") == "https://example/avatar.jpg"
    assert pin_avatar_url(None) is None
    assert AVATAR_ORIGIN in CSP_DIRECTIVES["img-src"]


async def test_fetch_profile_returns_none_when_no_player_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"response": {"players": []}}))

    assert await _client(handler).fetch_profile(STEAM_ID) is None


async def test_fetch_profile_swallows_failures_and_returns_none():
    """Sign-in only needs the SteamID; a profile-fetch outage must not block it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    assert await _client(handler).fetch_profile(STEAM_ID) is None


async def test_fetch_backpack_returns_the_raw_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {"result": {"status": 1, "items": [{"defindex": 378}]}}
            ),
        )

    result = await _client(handler).fetch_backpack(STEAM_ID)
    assert result == {"status": 1, "items": [{"defindex": 378}]}


async def test_fetch_backpack_folds_a_transport_failure_into_status_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    assert await _client(handler).fetch_backpack(STEAM_ID) == {"status": 0}
