"""HTTP behavior of GET /me/inventory.

Resolution logic is ``test_inventory.py``'s job; this file is about the route's
auth gating (503 unconfigured, 401 signed-out) and the refresh query param.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tf2_loadout.auth import SignedInUser
from tf2_loadout.catalog import CatalogService
from tf2_loadout.inventory import InventoryResult
from tf2_loadout.pricing import PricingService
from tf2_loadout.api import create_app

STEAM_ID = "76561197960287930"


class _StubAuth:
    async def complete_login(self, params):
        return SignedInUser(steam_id=STEAM_ID, persona="Spy", avatar=None, profile_url=None)

    def login_url(self, state: str) -> str:
        return f"https://steamcommunity.com/openid/login?state={state}"


class _StubInventory:
    def __init__(self, result: InventoryResult):
        self.result = result
        self.calls: list[bool] = []

    async def fetch(self, steam_id: str, *, refresh: bool = False) -> InventoryResult:
        self.calls.append(refresh)
        return self.result


def _client(auth=None, inventory=None) -> TestClient:
    catalog = CatalogService([])
    pricing = PricingService({})
    return TestClient(
        create_app(
            catalog,
            pricing,
            auth=auth,
            session_secret="test-secret",
            https_only=False,
            inventory=inventory,
        )
    )


def _signed_in_client(inventory=None) -> TestClient:
    client = _client(auth=_StubAuth(), inventory=inventory)
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    return client


def test_503_when_inventory_service_unconfigured():
    client = _signed_in_client(inventory=None)
    assert client.get("/me/inventory").status_code == 503


def test_401_when_signed_out():
    stub = _StubInventory(InventoryResult("ok", frozenset({1}), 0.0))
    client = _client(auth=_StubAuth(), inventory=stub)
    assert client.get("/me/inventory").status_code == 401


def test_healthz_reports_inventory_availability():
    stub = _StubInventory(InventoryResult("ok", frozenset(), 0.0))
    assert _client(auth=_StubAuth(), inventory=None).get("/healthz").json()["inventory"] is False
    assert _client(auth=_StubAuth(), inventory=stub).get("/healthz").json()["inventory"] is True


def test_returns_status_and_sorted_defindexes():
    stub = _StubInventory(InventoryResult("ok", frozenset({3, 1, 2}), 1700000000.0))
    client = _signed_in_client(inventory=stub)

    r = client.get("/me/inventory")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["defindexes"] == [1, 2, 3]
    assert body["fetched_at"] == 1700000000.0


def test_private_and_not_found_statuses_pass_through():
    stub = _StubInventory(InventoryResult("private", frozenset(), 0.0))
    client = _signed_in_client(inventory=stub)
    assert client.get("/me/inventory").json()["status"] == "private"


def test_refresh_query_param_is_forwarded():
    stub = _StubInventory(InventoryResult("ok", frozenset(), 0.0))
    client = _signed_in_client(inventory=stub)

    client.get("/me/inventory")
    client.get("/me/inventory", params={"refresh": "1"})

    assert stub.calls == [False, True]
