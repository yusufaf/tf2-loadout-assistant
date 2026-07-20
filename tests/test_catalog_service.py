"""Behavior of the in-memory cosmetic catalog query API.

CatalogService wraps a parsed list of cosmetics and answers the queries the agent
needs: lookup by defindex, filter by class, and name search.
"""

import json

import pytest

from tf2_loadout.models import Cosmetic
from tf2_loadout.catalog import (
    CACHE_VERSION,
    CatalogService,
    StaleCacheError,
    save_catalog_cache,
)


def _cosmetic(defindex, name, classes, regions=("hat",)):
    return Cosmetic(
        defindex=defindex,
        name=name,
        equip_regions=frozenset(regions),
        used_by_classes=tuple(classes),
    )


CATALOG = CatalogService(
    [
        _cosmetic(1, "Spy's Fedora", ["Spy"]),
        _cosmetic(2, "All-Class Cap", ["Scout", "Spy", "Soldier"]),
        _cosmetic(3, "Scout's Cap", ["Scout"]),
    ]
)


def test_for_class_returns_only_items_usable_by_that_class():
    spy_items = CATALOG.for_class("Spy")

    assert {c.defindex for c in spy_items} == {1, 2}


def test_for_class_is_case_insensitive():
    assert {c.defindex for c in CATALOG.for_class("spy")} == {1, 2}


def test_get_returns_item_by_defindex():
    assert CATALOG.get(3).name == "Scout's Cap"
    assert CATALOG.get(999) is None


# --- cache round-trip: the derived equip data must survive save/load intact ---

# Minimal items_game/schema pair: one paintable, style-bearing hat.
CACHE_ITEMS_GAME = {
    "equip_conflicts": {"whole_head": {"hat": "1"}},
    "prefabs": {},
    "items": {
        "116": {
            "name": "Modest Pile of Hat",
            "equip_region": "hat",
            "capabilities": {"paintable": "1"},
        }
    },
}
CACHE_SCHEMA_ITEMS = [
    {
        "defindex": 116,
        "item_class": "tf_wearable",
        "item_name": "The Modest Pile of Hat",
        "used_by_classes": ["Scout"],
        # Styles ride along in the schema cache, not the derived equip cache.
        "styles": [{"name": "Default"}],
    }
]


def test_cache_round_trip_preserves_item_attrs(tmp_path):
    save_catalog_cache(CACHE_SCHEMA_ITEMS, CACHE_ITEMS_GAME, tmp_path)

    catalog = CatalogService.from_cache(tmp_path)

    cosmetic = catalog.get(116)
    assert cosmetic.paintable is True
    assert cosmetic.styles == ("Default",)


def test_cache_round_trip_preserves_the_conflict_matrix(tmp_path):
    save_catalog_cache(CACHE_SCHEMA_ITEMS, CACHE_ITEMS_GAME, tmp_path)

    catalog = CatalogService.from_cache(tmp_path)

    assert "whole_head" in catalog.conflict_matrix["hat"]


def test_from_cache_rejects_a_cache_written_before_versioning(tmp_path):
    save_catalog_cache(CACHE_SCHEMA_ITEMS, CACHE_ITEMS_GAME, tmp_path)
    # Simulate a v1 cache: no version key, no attrs block.
    equip = json.loads((tmp_path / "equip.json").read_text(encoding="utf-8"))
    del equip["version"]
    del equip["attrs"]
    (tmp_path / "equip.json").write_text(json.dumps(equip), encoding="utf-8")

    with pytest.raises(StaleCacheError) as excinfo:
        CatalogService.from_cache(tmp_path)

    # The message must tell the operator how to fix it -- there is no refresh script.
    assert "pytest --live" in str(excinfo.value)
    assert str(CACHE_VERSION) in str(excinfo.value)
