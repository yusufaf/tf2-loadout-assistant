"""HTTP behavior of the /me/loadouts routes.

DynamoDB read/write logic is ``test_loadouts.py``'s job; this file is only about
auth gating (503 unconfigured, 401 signed-out) and request/response shape, using a
stub store in place of ``DynamoLoadoutStore`` -- same pattern as ``test_api_inventory.py``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tf2_loadout.auth import SignedInUser
from tf2_loadout.catalog import CatalogService
from tf2_loadout.loadouts import SavedLoadout
from tf2_loadout.pricing import PricingService
from tf2_loadout.api import create_app

STEAM_ID = "76561197960287930"


class _StubAuth:
    async def complete_login(self, params):
        return SignedInUser(steam_id=STEAM_ID, persona="Spy", avatar=None, profile_url=None)

    def login_url(self, state: str) -> str:
        return f"https://steamcommunity.com/openid/login?state={state}"


class _StubLoadouts:
    def __init__(self, seed: list[SavedLoadout] | None = None) -> None:
        self._by_steam_id: dict[str, list[SavedLoadout]] = {}
        if seed:
            self._by_steam_id[STEAM_ID] = list(seed)
        self.calls: list[tuple] = []

    async def list(self, steam_id: str) -> list[SavedLoadout]:
        self.calls.append(("list", steam_id))
        return list(self._by_steam_id.get(steam_id, []))

    async def create(self, steam_id, name, cls, defindexes) -> SavedLoadout:
        self.calls.append(("create", steam_id, name, cls, defindexes))
        loadout = SavedLoadout(
            id="new-id", name=name, cls=cls, defindexes=tuple(defindexes),
            created_at=1000, updated_at=1000,
        )
        self._by_steam_id.setdefault(steam_id, []).append(loadout)
        return loadout

    async def update(self, steam_id, loadout_id, *, name=None, cls=None, defindexes=None):
        self.calls.append(("update", steam_id, loadout_id, name, cls, defindexes))
        existing = self._by_steam_id.get(steam_id, [])
        for i, loadout in enumerate(existing):
            if loadout.id == loadout_id:
                updated = SavedLoadout(
                    id=loadout.id,
                    name=name if name is not None else loadout.name,
                    cls=cls if cls is not None else loadout.cls,
                    defindexes=tuple(defindexes) if defindexes is not None else loadout.defindexes,
                    created_at=loadout.created_at,
                    updated_at=2000,
                )
                existing[i] = updated
                return updated
        return None

    async def delete(self, steam_id, loadout_id) -> bool:
        self.calls.append(("delete", steam_id, loadout_id))
        existing = self._by_steam_id.get(steam_id, [])
        for loadout in existing:
            if loadout.id == loadout_id:
                existing.remove(loadout)
                return True
        return False


def _client(auth=None, loadouts=None) -> TestClient:
    catalog = CatalogService([])
    pricing = PricingService({})
    return TestClient(
        create_app(
            catalog,
            pricing,
            auth=auth,
            session_secret="test-secret",
            https_only=False,
            loadouts=loadouts,
        )
    )


def _signed_in_client(loadouts=None) -> TestClient:
    client = _client(auth=_StubAuth(), loadouts=loadouts)
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    return client


def test_503_when_loadouts_service_unconfigured():
    client = _signed_in_client(loadouts=None)
    assert client.get("/me/loadouts").status_code == 503
    assert client.post("/me/loadouts", json={"name": "x", "cls": "Spy"}).status_code == 503
    assert client.patch("/me/loadouts/1", json={"name": "x"}).status_code == 503
    assert client.delete("/me/loadouts/1").status_code == 503


def test_401_when_signed_out():
    stub = _StubLoadouts()
    client = _client(auth=_StubAuth(), loadouts=stub)
    assert client.get("/me/loadouts").status_code == 401
    assert client.post("/me/loadouts", json={"name": "x", "cls": "Spy"}).status_code == 401
    assert client.patch("/me/loadouts/1", json={"name": "x"}).status_code == 401
    assert client.delete("/me/loadouts/1").status_code == 401


def test_healthz_reports_loadouts_availability():
    assert _client(auth=_StubAuth(), loadouts=None).get("/healthz").json()["loadouts"] is False
    assert _client(auth=_StubAuth(), loadouts=_StubLoadouts()).get("/healthz").json()["loadouts"] is True


def test_list_returns_the_signed_in_players_loadouts():
    seed = [SavedLoadout("id1", "Cop look", "Spy", (1, 2), 1000, 1000)]
    client = _signed_in_client(loadouts=_StubLoadouts(seed))

    r = client.get("/me/loadouts")
    assert r.status_code == 200
    assert r.json() == {
        "loadouts": [
            {
                "id": "id1",
                "name": "Cop look",
                "cls": "Spy",
                "defindexes": [1, 2],
                "created_at": 1000,
                "updated_at": 1000,
            }
        ]
    }


def test_create_loadout():
    stub = _StubLoadouts()
    client = _signed_in_client(loadouts=stub)

    r = client.post("/me/loadouts", json={"name": "Cop look", "cls": "Spy", "defindexes": [1, 2]})
    assert r.status_code == 201
    assert r.json()["name"] == "Cop look"
    assert stub.calls == [("create", STEAM_ID, "Cop look", "Spy", [1, 2])]


def test_patch_renames_a_loadout():
    seed = [SavedLoadout("id1", "Old", "Spy", (1,), 1000, 1000)]
    client = _signed_in_client(loadouts=_StubLoadouts(seed))

    r = client.patch("/me/loadouts/id1", json={"name": "New"})
    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert r.json()["defindexes"] == [1]  # untouched


def test_patch_missing_loadout_404s():
    client = _signed_in_client(loadouts=_StubLoadouts())
    assert client.patch("/me/loadouts/nope", json={"name": "x"}).status_code == 404


def test_delete_loadout():
    seed = [SavedLoadout("id1", "A", "Spy", (1,), 1000, 1000)]
    client = _signed_in_client(loadouts=_StubLoadouts(seed))

    assert client.delete("/me/loadouts/id1").status_code == 204
    assert client.get("/me/loadouts").json() == {"loadouts": []}


def test_delete_missing_loadout_404s():
    client = _signed_in_client(loadouts=_StubLoadouts())
    assert client.delete("/me/loadouts/nope").status_code == 404
