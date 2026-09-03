"""Steam OpenID 2.0 sign-in.

Steam is one of the last OpenID **2.0** providers left on the web -- Authlib and
every other modern library speak OIDC only, so this is hand-rolled. The protocol
surface we actually need is small: build a login URL with four query params, send
the user to Steam, and verify the params Steam redirects back with. Only the last
step (a server-to-server POST back to Steam with ``mode`` swapped to
``check_authentication``) makes any of that trustworthy -- everything else on the
return URL passed through the user's browser and is forgeable.
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from . import __version__

OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
USER_AGENT = f"tf2-loadout-assistant/{__version__}"
DEFAULT_TIMEOUT = 30.0

# SteamID64 = 76561197960265728 + accountid. Accounts past ~294M carry the sum into
# 765612..., so the id space is 76561[0-9]{12} -- NOT the narrower 7656119[0-9]{10},
# which would silently reject real (if less common) accounts.
_CLAIMED_ID_RE = re.compile(r"^https?://steamcommunity\.com/openid/id/(76561[0-9]{12})$")

# How stale a response_nonce's leading timestamp may be. Narrows the replay window;
# it isn't the only defense (check_authentication + a one-shot CSRF state are).
_NONCE_MAX_AGE_SECONDS = 300


class SteamAuthError(RuntimeError):
    """Sign-in could not be verified."""


@dataclass(frozen=True)
class SteamIdentity:
    steam_id: str


class SteamOpenID:
    def __init__(
        self,
        *,
        return_to: str,
        realm: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._return_to = return_to
        self._realm = realm
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SteamOpenID":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def login_url(self, state: str) -> str:
        params = {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "checkid_setup",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.return_to": f"{self._return_to}?{urlencode({'state': state})}",
            "openid.realm": self._realm,
        }
        return f"{OPENID_ENDPOINT}?{urlencode(params)}"

    async def verify(self, params: dict[str, str]) -> str:
        """Verify a Steam return callback and return the caller's SteamID64.

        Cheap local guards run first; the network round-trip (the only step that
        actually proves the claim) runs last, so a forged/replayed callback never
        reaches Steam at all.
        """
        if params.get("openid.mode") != "id_res":
            # Steam sends "cancel" when the user backs out at the login screen --
            # that's a normal abort, not a forged callback, but it's still not a
            # successful sign-in either way.
            raise SteamAuthError(f"unexpected openid.mode {params.get('openid.mode')!r}")

        if params.get("openid.op_endpoint") != OPENID_ENDPOINT:
            raise SteamAuthError("unexpected openid.op_endpoint")

        claimed_id = params.get("openid.claimed_id", "")
        identity = params.get("openid.identity", "")
        if claimed_id != identity:
            raise SteamAuthError("claimed_id/identity mismatch")
        match = _CLAIMED_ID_RE.match(claimed_id)
        if not match:
            raise SteamAuthError("claimed_id is not a valid Steam profile URL")
        steam_id = match.group(1)

        return_to = params.get("openid.return_to", "")
        if not return_to.startswith(self._return_to):
            raise SteamAuthError("return_to does not match configured value")

        nonce = params.get("openid.response_nonce", "")
        timestamp = nonce.split("Z", 1)[0] + "Z" if "Z" in nonce else ""
        try:
            issued = time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise SteamAuthError("missing or malformed response_nonce")
        # calendar.timegm, not time.mktime: the struct is UTC (the "Z" suffix), and
        # mktime would wrongly reinterpret it in the local timezone.
        issued_epoch = calendar.timegm(issued)
        if abs(time.time() - issued_epoch) > _NONCE_MAX_AGE_SECONDS:
            raise SteamAuthError("response_nonce is stale")

        check_params = dict(params)
        check_params["openid.mode"] = "check_authentication"
        resp = await self._client.post(OPENID_ENDPOINT, data=check_params)
        resp.raise_for_status()
        if "is_valid:true" not in resp.text.splitlines():
            raise SteamAuthError("Steam rejected check_authentication")

        return steam_id
