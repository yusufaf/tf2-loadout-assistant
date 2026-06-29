"""Behavior of the equip-region conflict engine.

In TF2 two cosmetics cannot be worn together when they occupy the same equip
region (e.g. two hats both using "hat"). Conflict detection is therefore a
set-overlap test over each item's equip regions.
"""

from tf2_loadout.models import Cosmetic
from tf2_loadout.conflicts import find_conflicts


def _cosmetic(name: str, regions: list[str], defindex: int = 0) -> Cosmetic:
    return Cosmetic(defindex=defindex, name=name, equip_regions=frozenset(regions))


def test_items_sharing_a_region_conflict():
    hat_a = _cosmetic("Modest Pile of Hat", ["hat"], defindex=1)
    hat_b = _cosmetic("Ye Olde Baker Boy", ["hat"], defindex=2)

    conflicts = find_conflicts([hat_a, hat_b])

    assert len(conflicts) == 1
    pair = conflicts[0]
    assert {pair.a.defindex, pair.b.defindex} == {1, 2}
    assert "hat" in pair.regions


def test_items_in_different_regions_do_not_conflict():
    hat = _cosmetic("Modest Pile of Hat", ["hat"], defindex=1)
    glasses = _cosmetic("Pyro's Beanie", ["glasses"], defindex=2)

    assert find_conflicts([hat, glasses]) == []


def test_cross_region_conflicts_use_the_matrix():
    full_head = _cosmetic("Respectless Robo-Glove", ["whole_head"], defindex=1)
    hat = _cosmetic("Modest Pile of Hat", ["hat"], defindex=2)
    matrix = {
        "whole_head": frozenset({"hat", "face", "glasses"}),
        "hat": frozenset({"whole_head"}),
    }

    # whole_head and hat are different regions, so without the matrix they look fine...
    assert find_conflicts([full_head, hat]) == []
    # ...but the matrix says whole_head conflicts with hat.
    conflicts = find_conflicts([full_head, hat], conflict_matrix=matrix)
    assert len(conflicts) == 1


def test_multiple_overlapping_items_report_each_conflicting_pair():
    hat_a = _cosmetic("Hat A", ["hat"], defindex=1)
    hat_b = _cosmetic("Hat B", ["hat"], defindex=2)
    misc = _cosmetic("Some Misc", ["arms"], defindex=3)

    conflicts = find_conflicts([hat_a, hat_b, misc])

    # Only the two hats conflict; the misc occupies a disjoint region.
    assert len(conflicts) == 1
    assert {conflicts[0].a.defindex, conflicts[0].b.defindex} == {1, 2}
