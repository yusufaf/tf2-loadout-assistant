"""HTTP behavior of the loadout API.

The app is built from a catalog + pricing service so tests can inject small fixtures.
Cosmetic responses join in the price when one exists.
"""

from fastapi.testclient import TestClient

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


def test_conflicts_endpoint_flags_same_region():
    # defindex 1 and 3 both occupy "hat".
    r = _client().post("/loadout/conflicts", json={"defindexes": [1, 3]})
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    assert len(conflicts) == 1
    assert {conflicts[0]["a"], conflicts[0]["b"]} == {1, 3}
    assert "hat" in conflicts[0]["regions"]
