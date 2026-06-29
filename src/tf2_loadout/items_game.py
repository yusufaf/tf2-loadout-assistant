"""Parse Valve's items_game.txt (VDF) for equip-region data.

Two things the schema's GetSchemaItems endpoint lacks:
- per-item equip regions (often inherited through a ``prefab`` chain), and
- the ``equip_conflicts`` cross-region conflict matrix.
"""

from __future__ import annotations

_MAX_PREFAB_DEPTH = 8


def _own_regions(node: dict) -> frozenset[str]:
    """Equip regions declared directly on a node (item or prefab)."""
    out: set[str] = set()
    single = node.get("equip_region")
    if isinstance(single, str):
        out.add(single)
    elif isinstance(single, dict):  # rare duplicate-key form
        out |= set(single.keys())
    multi = node.get("equip_regions")
    if isinstance(multi, dict):
        out |= set(multi.keys())
    elif isinstance(multi, str):
        out.add(multi)
    return frozenset(out)


def _resolve(node: dict, prefabs: dict, depth: int = 0) -> frozenset[str]:
    own = _own_regions(node)
    if own or depth >= _MAX_PREFAB_DEPTH:
        return own
    inherited: set[str] = set()
    for name in node.get("prefab", "").split():
        prefab = prefabs.get(name)
        if isinstance(prefab, dict):
            inherited |= _resolve(prefab, prefabs, depth + 1)
    return frozenset(inherited)


def resolve_equip_regions(items_game: dict) -> dict[int, frozenset[str]]:
    """Map each item's defindex to its resolved equip regions.

    Items with no regions (directly or via prefab) are omitted.
    """
    prefabs = items_game.get("prefabs", {})
    result: dict[int, frozenset[str]] = {}
    for defindex, node in items_game.get("items", {}).items():
        if not isinstance(node, dict):
            continue
        regions = _resolve(node, prefabs)
        if regions:
            try:
                result[int(defindex)] = regions
            except (TypeError, ValueError):
                continue
    return result


def parse_conflict_matrix(items_game: dict) -> dict[str, frozenset[str]]:
    """Build a symmetric region -> conflicting-regions map from equip_conflicts."""
    adjacency: dict[str, set[str]] = {}
    for region, others in items_game.get("equip_conflicts", {}).items():
        for other in others:
            adjacency.setdefault(region, set()).add(other)
            adjacency.setdefault(other, set()).add(region)
    return {region: frozenset(conflicts) for region, conflicts in adjacency.items()}
