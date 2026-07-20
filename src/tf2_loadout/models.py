"""Core domain models for the loadout assistant."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Price:
    """A backpack.tf price for an item variant.

    ``value``/``value_high`` are denominated in ``currency`` (``"metal"`` refined or
    ``"keys"``); ``value_high`` is set only when the price is a range.
    """

    currency: str
    value: float
    value_high: float | None = None
    last_update: int | None = None


@dataclass(frozen=True)
class ItemAttrs:
    """Filterable item attributes resolved from items_game.txt.

    Separate from ``Cosmetic`` because these come from a different source than
    GetSchemaItems metadata and are resolved in one pass over the prefab tree.
    """

    paintable: bool = False
    holiday_restriction: str | None = None
    styles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cosmetic:
    """A TF2 cosmetic item.

    ``equip_regions`` is the set of body regions the item occupies; two cosmetics
    conflict when these sets overlap. Remaining fields are catalog metadata and are
    optional so the conflict engine can be exercised with minimal fixtures.
    """

    defindex: int
    name: str
    equip_regions: frozenset[str] = field(default_factory=frozenset)
    used_by_classes: tuple[str, ...] = ()
    item_slot: str | None = None
    image_url: str | None = None
    paintable: bool = False
    holiday_restriction: str | None = None
    styles: tuple[str, ...] = ()
