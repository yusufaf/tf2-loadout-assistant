"""HTTP behavior of the Steam auth routes.

Uses a stub in place of ``SteamAuthService`` -- the real verification logic is
``test_steam_auth.py``'s job; this file is only about session wiring, the CSRF
state round-trip, and the 503-when-unconfigured convention shared with lore/chat.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tf2_loadout.auth import SignedInUser, SteamAuthError
from tf2_loadout.catalog import CatalogService
from tf2_loadout.pricing import PricingService
from tf2_loadout.api import create_app

STEAM_ID = "76561197960287930"


class _StubAuth:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def login_url(self, state: str) -> str:
        return f"https://steamcommunity.com/openid/login?state={state}"

    async def complete_login(self, params: dict[str, str]):
        if self._fail:
            raise SteamAuthError("nope")
        return SignedInUser(
            steam_id=STEAM_ID, persona="Spy Enjoyer", avatar="a.jpg", profile_url="u"
        )


def _client(auth=None, session_secret: str | None = "test-secret") -> TestClient:
    catalog = CatalogService([])
    pricing = PricingService({})
    return TestClient(
        create_app(catalog, pricing, auth=auth, session_secret=session_secret, https_only=False)
    )


def test_auth_routes_503_when_no_auth_service_configured():
    client = _client(auth=None)
    assert client.get("/auth/steam/login", follow_redirects=False).status_code == 503
    assert client.get("/auth/steam/return", follow_redirects=False).status_code == 503
    assert client.get("/auth/me").status_code == 503
    assert client.post("/auth/logout").status_code == 503


def test_healthz_reports_auth_availability():
    assert _client(auth=None).get("/healthz").json()["auth"] is False
    assert _client(auth=_StubAuth()).get("/healthz").json()["auth"] is True


def test_me_reports_signed_out_by_default():
    client = _client(auth=_StubAuth())
    body = client.get("/auth/me").json()
    assert body == {"signed_in": False}


def test_login_redirects_to_steam_with_state():
    client = _client(auth=_StubAuth())
    r = client.get("/auth/steam/login", follow_redirects=False)
    assert r.status_code == 307
    assert "state=" in r.headers["location"]


def test_full_login_round_trip_sets_the_session():
    client = _client(auth=_StubAuth())
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]

    r = client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert r.headers["location"] == "/"

    me = client.get("/auth/me").json()
    assert me == {
        "signed_in": True,
        "steam_id": STEAM_ID,
        "persona": "Spy Enjoyer",
        "avatar": "a.jpg",
    }


def test_return_with_wrong_state_redirects_to_failure_without_calling_steam():
    client = _client(auth=_StubAuth())
    client.get("/auth/steam/login", follow_redirects=False)

    r = client.get(
        "/auth/steam/return",
        params={"state": "forged", "openid.mode": "id_res"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?auth=failed"
    assert client.get("/auth/me").json()["signed_in"] is False


def test_return_when_verification_fails_redirects_to_failure():
    client = _client(auth=_StubAuth(fail=True))
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]

    r = client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?auth=failed"


def test_logout_clears_the_session():
    client = _client(auth=_StubAuth())
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    assert client.get("/auth/me").json()["signed_in"] is True

    r = client.post("/auth/logout")
    assert r.status_code == 204
    assert client.get("/auth/me").json()["signed_in"] is False
