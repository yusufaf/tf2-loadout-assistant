"""Equip-region conflict detection.

Two cosmetics cannot be equipped together when they occupy the same equip region.
Detection is a set-overlap test over each item's ``equip_regions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from tf2_loadout.models import Cosmetic


@dataclass(frozen=True)
class Conflict:
    """A pair of cosmetics that cannot be worn together, and the shared regions."""

    a: Cosmetic
    b: Cosmetic
    regions: frozenset[str]


ConflictMatrix = dict[str, frozenset[str]]


def _conflicting_regions(
    a: frozenset[str], b: frozenset[str], matrix: ConflictMatrix
) -> frozenset[str]:
    """Regions of ``a`` that conflict with any region of ``b``.

    A region conflicts with another when they are identical or listed against each
    other in the cross-region matrix.
    """
    out: set[str] = set()
    for ra in a:
        for rb in b:
            if ra == rb or rb in matrix.get(ra, ()) or ra in matrix.get(rb, ()):
                out.add(ra)
                out.add(rb)
    return frozenset(out)


def find_conflicts(
    items: list[Cosmetic], conflict_matrix: ConflictMatrix | None = None
) -> list[Conflict]:
    """Return every pair of items whose equip regions conflict.

    Same-region overlap always conflicts; ``conflict_matrix`` adds cross-region
    conflicts (e.g. ``whole_head`` vs ``hat``).
    """
    matrix = conflict_matrix or {}
    conflicts: list[Conflict] = []
    for a, b in combinations(items, 2):
        shared = _conflicting_regions(a.equip_regions, b.equip_regions, matrix)
        if shared:
            conflicts.append(Conflict(a=a, b=b, regions=shared))
    return conflicts
