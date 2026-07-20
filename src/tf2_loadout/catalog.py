"""TF2 cosmetic catalog: parse the Valve item schema into ``Cosmetic`` objects.

Source: Steam ``IEconItems_440/GetSchemaItems``. Each raw item is a dict; we keep the
ones that occupy an equip region (cosmetics) and drop weapons/tools.
"""

from __future__ import annotations

import json
from pathlib import Path

from tf2_loadout.conflicts import Conflict, find_conflicts
from tf2_loadout.items_game import (
    parse_conflict_matrix,
    resolve_equip_regions,
    resolve_item_attrs,
)
from tf2_loadout.models import Cosmetic, ItemAttrs


def _equip_regions(raw: dict) -> frozenset[str]:
    """Read equip regions, tolerating both the array and single-string schema forms."""
    if "equip_regions" in raw:
        return frozenset(raw["equip_regions"])
    if "equip_region" in raw:
        return frozenset({raw["equip_region"]})
    return frozenset()


def _to_cosmetic(raw: dict) -> Cosmetic:
    return Cosmetic(
        defindex=raw["defindex"],
        name=raw.get("item_name") or raw["name"],
        equip_regions=_equip_regions(raw),
        used_by_classes=tuple(raw.get("used_by_classes", ())),
        item_slot=raw.get("item_slot"),
        image_url=raw.get("image_url"),
    )


def parse_schema_items(raw_items: list[dict]) -> list[Cosmetic]:
    """Parse raw schema items into cosmetics, dropping items with no equip region."""
    return [
        _to_cosmetic(raw) for raw in raw_items if _equip_regions(raw)
    ]


_NO_ATTRS = ItemAttrs()


def _styles(raw: dict) -> tuple[str, ...]:
    """Style variant names for a schema item, in declaration order.

    Styles live in GetSchemaItems, not items_game -- Valve's items_game feed carries no
    style data at all. The value is a list of ``{"name": ...}`` objects. A handful of
    items expose untranslated localization tokens (``TF_UnknownStyle``); they pass
    through as-is rather than being guessed at.
    """
    styles = raw.get("styles")
    if not isinstance(styles, list):
        return ()
    return tuple(
        s["name"]
        for s in styles
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    )


def merge_catalog(
    schema_items: list[dict],
    equip_regions: dict[int, frozenset[str]],
    attrs: dict[int, ItemAttrs] | None = None,
) -> list[Cosmetic]:
    """Build cosmetics by merging GetSchemaItems metadata with items_game data.

    A cosmetic must be a wearable (``item_class`` starting ``tf_wearable``) and have
    resolved equip regions; this excludes weapons that happen to carry a region
    (e.g. mediguns) and wearables whose regions could not be resolved.

    ``attrs`` is sparse: only items with a non-default attribute appear in it.
    """
    attrs = attrs or {}
    cosmetics: list[Cosmetic] = []
    for raw in schema_items:
        if not str(raw.get("item_class", "")).startswith("tf_wearable"):
            continue
        regions = equip_regions.get(raw["defindex"])
        if not regions:
            continue
        item_attrs = attrs.get(raw["defindex"], _NO_ATTRS)
        cosmetics.append(
            Cosmetic(
                defindex=raw["defindex"],
                name=raw.get("item_name") or raw["name"],
                equip_regions=regions,
                used_by_classes=tuple(raw.get("used_by_classes", ())),
                item_slot=raw.get("item_slot"),
                image_url=raw.get("image_url"),
                paintable=item_attrs.paintable,
                holiday_restriction=item_attrs.holiday_restriction,
                styles=_styles(raw),
            )
        )
    return cosmetics


SCHEMA_CACHE = "schema_items.json"
EQUIP_CACHE = "equip.json"

# Bump whenever equip.json gains or changes a field. A cache written by an older version
# would otherwise load fine and quietly report every item as unpaintable, style-less and
# unrestricted -- wrong answers are worse than a loud failure.
CACHE_VERSION = 3


class StaleCacheError(RuntimeError):
    """Raised when the on-disk cache predates the current cache format."""


def save_schema_items(raw_items: list[dict], path: str | Path) -> None:
    """Persist the raw schema items to a local JSON cache."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw_items), encoding="utf-8")


def save_catalog_cache(
    schema_items: list[dict], items_game: dict, cache_dir: str | Path
) -> None:
    """Cache everything needed to rebuild the catalog offline.

    Stores raw GetSchemaItems plus the *derived* equip data (regions, conflict matrix
    and filterable attributes) — far smaller than the full items_game.txt.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    save_schema_items(schema_items, cache_dir / SCHEMA_CACHE)
    equip = {
        "version": CACHE_VERSION,
        "regions": {
            str(di): sorted(rs)
            for di, rs in resolve_equip_regions(items_game).items()
        },
        "matrix": {
            region: sorted(conf)
            for region, conf in parse_conflict_matrix(items_game).items()
        },
        "attrs": {
            str(di): {
                "paintable": a.paintable,
                "holiday_restriction": a.holiday_restriction,
            }
            for di, a in resolve_item_attrs(items_game).items()
        },
    }
    (cache_dir / EQUIP_CACHE).write_text(json.dumps(equip), encoding="utf-8")


class CatalogService:
    """In-memory query API over a parsed cosmetic catalog."""

    def __init__(
        self,
        cosmetics: list[Cosmetic],
        conflict_matrix: dict[str, frozenset[str]] | None = None,
    ):
        self._cosmetics = list(cosmetics)
        self._by_defindex = {c.defindex: c for c in self._cosmetics}
        self._conflict_matrix = conflict_matrix or {}

    def __len__(self) -> int:
        return len(self._cosmetics)

    @property
    def conflict_matrix(self) -> dict[str, frozenset[str]]:
        """The cross-region conflict matrix, for shipping to clients."""
        return dict(self._conflict_matrix)

    @classmethod
    def from_schema_items(cls, raw_items: list[dict]) -> "CatalogService":
        return cls(parse_schema_items(raw_items))

    @classmethod
    def build_from_sources(
        cls, schema_items: list[dict], items_game: dict
    ) -> "CatalogService":
        """Build the catalog by merging GetSchemaItems with items_game equip data."""
        regions = resolve_equip_regions(items_game)
        matrix = parse_conflict_matrix(items_game)
        attrs = resolve_item_attrs(items_game)
        return cls(merge_catalog(schema_items, regions, attrs), conflict_matrix=matrix)

    def conflicts(self, cosmetics: list[Cosmetic]) -> list[Conflict]:
        """Detect equip conflicts among the given cosmetics using the region matrix."""
        return find_conflicts(cosmetics, conflict_matrix=self._conflict_matrix)

    @classmethod
    def from_cache(cls, cache_dir: str | Path) -> "CatalogService":
        """Rebuild the catalog from a directory written by ``save_catalog_cache``."""
        cache_dir = Path(cache_dir)
        schema_items = json.loads(
            (cache_dir / SCHEMA_CACHE).read_text(encoding="utf-8")
        )
        equip = json.loads((cache_dir / EQUIP_CACHE).read_text(encoding="utf-8"))
        version = equip.get("version", 1)
        if version != CACHE_VERSION:
            raise StaleCacheError(
                f"catalog cache is v{version}, expected v{CACHE_VERSION} — "
                "rebuild it with `uv run pytest --live`"
            )
        regions = {
            int(di): frozenset(rs) for di, rs in equip["regions"].items()
        }
        matrix = {
            region: frozenset(conf) for region, conf in equip["matrix"].items()
        }
        attrs = {
            int(di): ItemAttrs(
                paintable=a["paintable"],
                holiday_restriction=a["holiday_restriction"],
            )
            for di, a in equip["attrs"].items()
        }
        return cls(merge_catalog(schema_items, regions, attrs), conflict_matrix=matrix)

    def all(self) -> list[Cosmetic]:
        return list(self._cosmetics)

    def get(self, defindex: int) -> Cosmetic | None:
        return self._by_defindex.get(defindex)

    def search(self, text: str) -> list[Cosmetic]:
        needle = text.lower()
        return [c for c in self._cosmetics if needle in c.name.lower()]

    def for_class(self, class_name: str) -> list[Cosmetic]:
        wanted = class_name.lower()
        return [
            c
            for c in self._cosmetics
            if any(cls.lower() == wanted for cls in c.used_by_classes)
        ]
