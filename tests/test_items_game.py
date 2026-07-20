"""Behavior of the items_game.txt parser (equip regions + conflict matrix).

Per-item equip regions may be declared directly or inherited through a chain of
``prefab`` references. The ``equip_conflicts`` block defines cross-region conflicts on
top of the implicit same-region conflict.
"""

from tf2_loadout.items_game import (
    parse_conflict_matrix,
    resolve_equip_regions,
    resolve_item_attrs,
)

# Shape mirrors vdf.loads(items_game.txt)["items_game"]: all scalars are strings,
# blocks are nested dicts.
FIXTURE = {
    "equip_conflicts": {
        "glasses": {"face": "1", "lenses": "1"},
        "whole_head": {"hat": "1", "face": "1", "glasses": "1"},
    },
    # "prefab" values are space-separated lists of prefab names (e.g. "valve hat").
    "prefabs": {
        "valve": {"craft_class": "hat"},  # base prefab, no region
        "hat": {"equip_region": "hat"},
        "fancy": {"prefab": "hat"},  # nested
        "paintable_hat": {
            "equip_region": "hat",
            "capabilities": {"paintable": "1"},
        },
        "spooky": {
            "equip_region": "hat",
            "holiday_restriction": "halloween_or_fullmoon",
        },
    },
    "items": {
        "1": {"name": "Direct Hat", "equip_region": "hat"},
        "2": {"name": "Multi", "equip_regions": {"hat": "1", "glasses": "1"}},
        "3": {"name": "Prefab Hat", "prefab": "valve hat"},
        "4": {"name": "Nested Prefab", "prefab": "fancy"},
        "5": {"name": "Weapon", "item_class": "tf_weapon_x"},
        "6": {
            "name": "Own Paintable",
            "equip_region": "hat",
            "capabilities": {"paintable": "1"},
        },
        "7": {"name": "Inherited Paintable", "prefab": "paintable_hat"},
        "8": {"name": "Inherited Spooky", "prefab": "spooky"},
        "10": {
            "name": "Explicitly Not Paintable",
            "prefab": "paintable_hat",
            "capabilities": {"paintable": "0"},
            # Carries a restriction too, so the item survives the all-defaults filter
            # and the override is actually observable.
            "holiday_restriction": "halloween_or_fullmoon",
        },
    },
}


def test_resolves_regions_direct_multi_and_via_prefab():
    regions = resolve_equip_regions(FIXTURE)

    assert regions[1] == frozenset({"hat"})
    assert regions[2] == frozenset({"hat", "glasses"})
    assert regions[3] == frozenset({"hat"})  # inherited from "valve hat"
    assert regions[4] == frozenset({"hat"})  # inherited through nested prefab
    assert 5 not in regions  # no region anywhere -> excluded


def test_conflict_matrix_is_symmetric():
    matrix = parse_conflict_matrix(FIXTURE)

    # declared: whole_head -> hat
    assert "hat" in matrix["whole_head"]
    # and the reverse direction is filled in
    assert "whole_head" in matrix["hat"]
    assert "lenses" in matrix["glasses"]
    assert "glasses" in matrix["lenses"]


def test_resolves_paintable_directly_and_via_prefab():
    attrs = resolve_item_attrs(FIXTURE)

    assert attrs[6].paintable is True
    assert attrs[7].paintable is True  # inherited from the prefab
    assert 1 not in attrs  # plain hat declares nothing -> omitted


def test_own_capabilities_override_the_prefab():
    attrs = resolve_item_attrs(FIXTURE)

    # capabilities is a block, so the item's own block shadows the prefab's entirely.
    assert attrs[10].paintable is False


def test_resolves_holiday_restriction_via_prefab():
    attrs = resolve_item_attrs(FIXTURE)

    assert attrs[8].holiday_restriction == "halloween_or_fullmoon"


def test_ignores_items_declaring_nothing_filterable():
    attrs = resolve_item_attrs(FIXTURE)

    # Style variants are absent from items_game entirely -- they come from
    # GetSchemaItems, so nothing here should record them (see test_catalog.py).
    assert set(attrs) == {6, 7, 8, 10}
