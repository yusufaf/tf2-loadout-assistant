"""HTTP behavior of the loadout API.

The app is built from a catalog + pricing service so tests can inject small fixtures.
Cosmetic responses join in the price when one exists.
"""

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_agent
from tf2_loadout.models import Cosmetic, Price
from tf2_loadout.catalog import CatalogService
from tf2_loadout.lore import ItemLore, LoreService
from tf2_loadout.pricing import PricingService
from tf2_loadout.api import create_app


class _StubWiki:
    async def query(self, **params):
        return {
            "parse": {
                "title": "Spy Fedora",
                "wikitext": "'''The Spy Fedora''' is a dapper cosmetic item for the Spy.",
            }
        }


def _client() -> TestClient:
    catalog = CatalogService(
        [
            Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1"),
            Cosmetic(2, "Spy Shades", frozenset({"glasses"}), ("Spy",), "misc", "img2"),
            Cosmetic(3, "Scout Cap", frozenset({"hat"}), ("Scout",), "misc", "img3"),
        ]
    )
    pricing = PricingService({1: Price("metal", 5.0, 6.0)})
    lore = LoreService(_StubWiki())
    return TestClient(create_app(catalog, pricing, lore))


def test_healthz_reports_counts():
    r = _client().get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cosmetics"] == 3
    assert body["priced"] == 1


def test_cosmetics_filtered_by_class_join_prices():
    r = _client().get("/cosmetics", params={"used_by": "Spy"})
    assert r.status_code == 200
    items = {c["defindex"]: c for c in r.json()["items"]}
    assert set(items) == {1, 2}
    assert items[1]["price"] == {"currency": "metal", "value": 5.0, "value_high": 6.0}
    assert items[2]["price"] is None  # no price known


def test_get_single_cosmetic_and_404():
    client = _client()
    assert client.get("/cosmetics/3").json()["name"] == "Scout Cap"
    assert client.get("/cosmetics/999").status_code == 404


def test_lore_endpoint_returns_summary():
    r = _client().get("/lore/1")
    assert r.status_code == 200
    assert "dapper cosmetic item for the Spy" in r.json()["summary"]


def _chat_client() -> TestClient:
    """A client whose agent always answers with a fixed structured reply."""

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "Try the fedora.", "suggested_defindexes": [1]},
                )
            ]
        )

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = LoadoutAgentService(
        build_agent(FunctionModel(model_fn)),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    return TestClient(create_app(catalog, pricing, None, chat))


def test_chat_returns_503_when_no_agent_configured():
    r = _client().post("/chat", json={"message": "dress my Spy"})
    assert r.status_code == 503
    assert "chat" in r.json()["detail"].lower()


def test_healthz_reports_chat_availability():
    assert _client().get("/healthz").json()["chat"] is False
    assert _chat_client().get("/healthz").json()["chat"] is True


def test_chat_returns_message_and_suggestions():
    r = _chat_client().post("/chat", json={"message": "dress my Spy"})
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "Try the fedora."
    assert body["suggested_defindexes"] == [1]
    assert body["history"]


def test_chat_drops_defindexes_that_are_not_in_the_catalog():
    """Weaker models name items they never looked up; the API must not pass them on."""

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    # 1 is real, 999 is invented.
                    {"message": "Try these.", "suggested_defindexes": [1, 999]},
                )
            ]
        )

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = LoadoutAgentService(
        build_agent(FunctionModel(model_fn)),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))
    r = client.post("/chat", json={"message": "dress my Spy"})
    assert r.json()["suggested_defindexes"] == [1]


def test_chat_history_round_trips():
    client = _chat_client()
    first = client.post("/chat", json={"message": "dress my Spy"}).json()
    second = client.post(
        "/chat", json={"message": "and a hat?", "history": first["history"]}
    )
    assert second.status_code == 200
    assert len(second.json()["history"]) > len(first["history"])


def test_chat_rejects_oversized_history():
    client = _chat_client()
    r = client.post("/chat", json={"message": "hi", "history": [{}] * 200})
    assert r.status_code == 422


def test_conflicts_endpoint_flags_same_region():
    # defindex 1 and 3 both occupy "hat".
    r = _client().post("/loadout/conflicts", json={"defindexes": [1, 3]})
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    assert len(conflicts) == 1
    assert {conflicts[0]["a"], conflicts[0]["b"]} == {1, 3}
    assert "hat" in conflicts[0]["regions"]
