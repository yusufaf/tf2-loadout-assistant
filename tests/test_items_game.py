"""Behavior of the items_game.txt parser (equip regions + conflict matrix).

Per-item equip regions may be declared directly or inherited through a chain of
``prefab`` references. The ``equip_conflicts`` block defines cross-region conflicts on
top of the implicit same-region conflict.
"""

from tf2_loadout.items_game import resolve_equip_regions, parse_conflict_matrix

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
    },
    "items": {
        "1": {"name": "Direct Hat", "equip_region": "hat"},
        "2": {"name": "Multi", "equip_regions": {"hat": "1", "glasses": "1"}},
        "3": {"name": "Prefab Hat", "prefab": "valve hat"},
        "4": {"name": "Nested Prefab", "prefab": "fancy"},
        "5": {"name": "Weapon", "item_class": "tf_weapon_x"},
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
