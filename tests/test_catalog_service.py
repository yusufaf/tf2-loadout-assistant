"""Behavior of the in-memory cosmetic catalog query API.

CatalogService wraps a parsed list of cosmetics and answers the queries the agent
needs: lookup by defindex, filter by class, and name search.
"""

from tf2_loadout.models import Cosmetic
from tf2_loadout.catalog import CatalogService


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
