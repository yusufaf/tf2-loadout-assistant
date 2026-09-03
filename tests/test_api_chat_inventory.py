"""The /chat routes must hand the agent the signed-in player's real backpack.

Inventory resolution itself is ``test_inventory.py``'s job; the agent's use of
``owned`` is ``test_agent.py``'s. This file is only about the wiring between a
session cookie and what ends up in the agent's instructions.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_agent
from tf2_loadout.auth import SignedInUser
from tf2_loadout.catalog import CatalogService
from tf2_loadout.inventory import InventoryResult
from tf2_loadout.models import Cosmetic
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

    async def fetch(self, steam_id: str, *, refresh: bool = False) -> InventoryResult:
        return self.result


def _echoing_model_fn(messages, info):
    """Echoes the agent's instructions back as the reply, so a test can assert on it
    without needing a tool call round-trip."""
    instructions = next(
        (m.instructions for m in messages if getattr(m, "instructions", None)), ""
    )
    return ModelResponse(
        parts=[
            ToolCallPart(
                info.output_tools[0].name,
                {"message": instructions, "suggested_defindexes": []},
            )
        ]
    )


async def _echoing_stream_fn(messages, info):
    """Streaming counterpart of ``_echoing_model_fn`` -- /chat/stream forces the
    model into streaming mode, which FunctionModel needs a separate hook for."""
    instructions = next(
        (m.instructions for m in messages if getattr(m, "instructions", None)), ""
    )
    args = json.dumps({"message": instructions, "suggested_defindexes": []})
    yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args=args)}


def _client(auth=None, inventory=None) -> TestClient:
    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = LoadoutAgentService(
        build_agent(
            FunctionModel(_echoing_model_fn, stream_function=_echoing_stream_fn)
        ),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    return TestClient(
        create_app(
            catalog,
            pricing,
            chat=chat,
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


def test_signed_out_chat_sees_no_inventory():
    client = _client(auth=_StubAuth(), inventory=_StubInventory(InventoryResult("ok", frozenset({1}), 0.0)))
    r = client.post("/chat", json={"message": "dress me from what I own"})
    assert "not available this turn" in r.json()["message"]


def test_signed_in_with_ok_inventory_reaches_the_agent():
    stub = _StubInventory(InventoryResult("ok", frozenset({1}), 0.0))
    client = _signed_in_client(inventory=stub)
    r = client.post("/chat", json={"message": "dress me from what I own"})
    assert "owns 1" in r.json()["message"]


def test_signed_in_with_private_backpack_reads_as_unavailable():
    stub = _StubInventory(InventoryResult("private", frozenset(), 0.0))
    client = _signed_in_client(inventory=stub)
    r = client.post("/chat", json={"message": "dress me from what I own"})
    assert "not available this turn" in r.json()["message"]


def test_no_inventory_service_configured_reads_as_unavailable():
    client = _signed_in_client(inventory=None)
    r = client.post("/chat", json={"message": "dress me from what I own"})
    assert "not available this turn" in r.json()["message"]


def test_chat_stream_also_receives_owned_inventory():
    stub = _StubInventory(InventoryResult("ok", frozenset({1}), 0.0))
    client = _signed_in_client(inventory=stub)
    r = client.post("/chat/stream", json={"message": "dress me from what I own"})
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    final = lines[-1]
    assert final["kind"] == "final"
    assert "owns 1" in final["message"]
