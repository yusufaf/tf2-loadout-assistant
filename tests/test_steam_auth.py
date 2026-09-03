"""Steam OpenID 2.0 verification.

The whole point of ``SteamOpenID.verify`` is to catch a forged or replayed return
callback -- one case per guard, plus the happy path where every guard passes and
Steam's check_authentication says yes.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tf2_loadout.steam_auth import SteamAuthError, SteamOpenID

RETURN_TO = "https://tf2.example.dev/auth/steam/return"
REALM = "https://tf2.example.dev"
STEAM_ID = "76561197960287930"


def _nonce(offset_seconds: float = 0.0) -> str:
    t = time.gmtime(time.time() - offset_seconds)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t) + "abcdef"


def _good_params(**overrides: str) -> dict[str, str]:
    params = {
        "openid.mode": "id_res",
        "openid.op_endpoint": "https://steamcommunity.com/openid/login",
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{STEAM_ID}",
        "openid.return_to": f"{RETURN_TO}?state=xyz",
        "openid.response_nonce": _nonce(),
    }
    params.update(overrides)
    return params


def _client(is_valid: bool = True, transport: httpx.MockTransport | None = None) -> SteamOpenID:
    if transport is None:

        def handler(request: httpx.Request) -> httpx.Response:
            body = "ns:http://specs.openid.net/auth/2.0\n"
            body += "is_valid:true\n" if is_valid else "is_valid:false\n"
            return httpx.Response(200, text=body)

        transport = httpx.MockTransport(handler)
    return SteamOpenID(return_to=RETURN_TO, realm=REALM, transport=transport)


async def test_happy_path_returns_steam_id():
    steam_id = await _client().verify(_good_params())
    assert steam_id == STEAM_ID


async def test_login_url_carries_realm_and_return_to():
    url = _client().login_url("state123")
    assert "openid.mode=checkid_setup" in url
    assert "state123" in url
    assert "openid.realm=https%3A%2F%2Ftf2.example.dev" in url


async def test_cancel_mode_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(_good_params(**{"openid.mode": "cancel"}))


async def test_wrong_op_endpoint_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(
            _good_params(**{"openid.op_endpoint": "https://evil.example/openid/login"})
        )


async def test_claimed_id_identity_mismatch_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(
            _good_params(
                **{"openid.identity": "https://steamcommunity.com/openid/id/76561197960000001"}
            )
        )


async def test_claimed_id_outside_the_steamid_pattern_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(
            _good_params(
                **{
                    "openid.claimed_id": "https://steamcommunity.com/openid/id/notasteamid",
                    "openid.identity": "https://steamcommunity.com/openid/id/notasteamid",
                }
            )
        )


async def test_claimed_id_past_the_original_id_range_is_accepted():
    # 76561197960265728 + accountid can carry into 765612... past ~294M accounts;
    # 7656119[0-9]{10} would wrongly reject this.
    wide_id = "76561220000000000"
    steam_id = await _client().verify(
        _good_params(
            **{
                "openid.claimed_id": f"https://steamcommunity.com/openid/id/{wide_id}",
                "openid.identity": f"https://steamcommunity.com/openid/id/{wide_id}",
            }
        )
    )
    assert steam_id == wide_id


async def test_mismatched_return_to_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(
            _good_params(**{"openid.return_to": "https://evil.example/auth/steam/return"})
        )


async def test_stale_nonce_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(_good_params(**{"openid.response_nonce": _nonce(600)}))


async def test_malformed_nonce_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client().verify(_good_params(**{"openid.response_nonce": "garbage"}))


async def test_failed_check_authentication_is_rejected():
    with pytest.raises(SteamAuthError):
        await _client(is_valid=False).verify(_good_params())
