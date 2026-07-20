"""Parse Valve's items_game.txt (VDF) for equip-region data.

Three things the schema's GetSchemaItems endpoint lacks:
- per-item equip regions (often inherited through a ``prefab`` chain),
- the ``equip_conflicts`` cross-region conflict matrix, and
- filterable attributes (paintability, holiday restriction, style variants).
"""

from __future__ import annotations

from tf2_loadout.models import ItemAttrs

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


def _flatten_with_prefabs(node: dict, prefabs: dict, depth: int = 0) -> dict:
    """Merge a node with everything it inherits, own keys winning.

    Prefabs are applied left to right, so a later prefab in the ``prefab`` list beats an
    earlier one, and the node's own keys beat all of them. Merging is shallow: a block
    the node declares replaces the inherited block outright rather than being merged key
    by key, which matches how Valve's tooling treats these overrides.

    Deliberately NOT used for equip regions -- see ``_resolve``, which must not inherit
    once a node declares any region of its own.
    """
    if depth >= _MAX_PREFAB_DEPTH:
        return dict(node)
    merged: dict = {}
    for name in node.get("prefab", "").split():
        prefab = prefabs.get(name)
        if isinstance(prefab, dict):
            merged.update(_flatten_with_prefabs(prefab, prefabs, depth + 1))
    merged.update(node)
    return merged


def _style_names(node: dict) -> tuple[str, ...]:
    """Style display names, in declaration order.

    The ``styles`` block is keyed by style index; each value is normally a block with a
    ``name``. Unknown shapes yield no styles rather than raising.
    """
    styles = node.get("styles")
    if not isinstance(styles, dict):
        return ()
    names: list[str] = []
    for style in styles.values():
        if isinstance(style, dict) and isinstance(style.get("name"), str):
            names.append(style["name"])
    return tuple(names)


def _attrs(node: dict) -> ItemAttrs:
    capabilities = node.get("capabilities")
    paintable = isinstance(capabilities, dict) and capabilities.get("paintable") == "1"
    restriction = node.get("holiday_restriction")
    return ItemAttrs(
        paintable=paintable,
        holiday_restriction=restriction if isinstance(restriction, str) else None,
        styles=_style_names(node),
    )


_NO_ATTRS = ItemAttrs()


def resolve_item_attrs(items_game: dict) -> dict[int, ItemAttrs]:
    """Map each item's defindex to its resolved filterable attributes.

    Items whose attributes are all defaults are omitted, keeping the cache small -- most
    of the schema is unpainted, unrestricted and style-less.
    """
    prefabs = items_game.get("prefabs", {})
    result: dict[int, ItemAttrs] = {}
    for defindex, node in items_game.get("items", {}).items():
        if not isinstance(node, dict):
            continue
        attrs = _attrs(_flatten_with_prefabs(node, prefabs))
        if attrs == _NO_ATTRS:
            continue
        try:
            result[int(defindex)] = attrs
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
