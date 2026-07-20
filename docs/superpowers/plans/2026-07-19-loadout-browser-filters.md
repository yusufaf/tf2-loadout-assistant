# Loadout Browser Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the cosmetic browser class-scope, sort, and six filter toggles, matching loadout.tf's browsing power without a 3D viewer.

**Architecture:** Three new item attributes (paintable, holiday restriction, styles) are parsed from `items_game.txt` through a new prefab-flattening helper and flow into `Cosmetic`. The API ships the equip-conflict matrix as a static document and can return an unlimited class list. All filtering then runs client-side in two pure TypeScript modules, so every toggle is instant.

**Tech Stack:** Python 3.12 / FastAPI / pytest (backend); React 19 / TypeScript / Vite / Vitest (frontend); `uv` and `pnpm` as package managers.

## Global Constraints

- `from __future__ import annotations` at the top of every Python module.
- Frozen dataclasses for value types; Pydantic models for anything crossing the API boundary.
- Module docstrings explain *why*. Comments mark where an external schema is weird.
- Never remove the session fixture in `tests/conftest.py` that pins `ALLOW_MODEL_REQUESTS = False`.
- Backend tests: `uv run pytest`. Never add a test that reaches a real API without the `live` marker.
- Frontend tests: `cd frontend && pnpm test`.
- Prefab resolution depth cap stays at 8 (`_MAX_PREFAB_DEPTH`).
- The spec is `docs/superpowers/specs/2026-07-19-loadout-browser-filters-design.md`.

### Deviation from the spec, decided during planning

The spec proposed moving `resolve_equip_regions` onto a shared `_flatten_with_prefabs`
helper. **Do not do this.** Today `_resolve` returns a node's own regions *without*
inheriting when the node declares any. A generic key-level merge would instead union an
item's own `equip_region` with its prefab's `equip_regions` — a different answer for items
that use the two spellings across a prefab boundary. Region resolution is load-bearing and
correct; leave it alone. The new flattener serves only the three new attributes.

---

### Task 1: Parse paintable, holiday restriction, and styles from items_game

**Files:**
- Modify: `src/tf2_loadout/models.py`
- Modify: `src/tf2_loadout/items_game.py`
- Test: `tests/test_items_game.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `tf2_loadout.models.ItemAttrs` — frozen dataclass with fields `paintable: bool = False`, `holiday_restriction: str | None = None`, `styles: tuple[str, ...] = ()`.
  - `tf2_loadout.items_game.resolve_item_attrs(items_game: dict) -> dict[int, ItemAttrs]` — every item that declares or inherits at least one non-default attribute. Items with all-default attributes are omitted.

- [ ] **Step 1: Write the failing tests**

Add to the `FIXTURE` dict in `tests/test_items_game.py`. Replace the existing `prefabs` and
`items` blocks with these (keep `equip_conflicts` exactly as it is):

```python
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
        "9": {
            "name": "Styled",
            "equip_region": "hat",
            "styles": {
                "0": {"name": "Default"},
                "1": {"name": "Rogue"},
            },
        },
        "10": {
            "name": "Explicitly Not Paintable",
            "prefab": "paintable_hat",
            "capabilities": {"paintable": "0"},
            # Carries a restriction too, so the item survives the all-defaults filter
            # and the override is actually observable.
            "holiday_restriction": "halloween_or_fullmoon",
        },
        "11": {
            "name": "Weird Styles",
            "equip_region": "hat",
            "styles": "not_a_block",  # tolerate unexpected shapes
        },
    },
```

Then append these tests to the same file:

```python
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


def test_reads_style_names_in_declaration_order():
    attrs = resolve_item_attrs(FIXTURE)

    assert attrs[9].styles == ("Default", "Rogue")


def test_tolerates_a_styles_value_that_is_not_a_block():
    attrs = resolve_item_attrs(FIXTURE)

    assert 11 not in attrs  # nothing parseable -> no attrs recorded, no exception
```

Update the import at the top of the file:

```python
from tf2_loadout.items_game import (
    parse_conflict_matrix,
    resolve_equip_regions,
    resolve_item_attrs,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_items_game.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_item_attrs'`

- [ ] **Step 3: Add the ItemAttrs model**

Append to `src/tf2_loadout/models.py`:

```python
@dataclass(frozen=True)
class ItemAttrs:
    """Filterable item attributes resolved from items_game.txt.

    Separate from ``Cosmetic`` because these come from a different source than
    GetSchemaItems metadata and are resolved in one pass over the prefab tree.
    """

    paintable: bool = False
    holiday_restriction: str | None = None
    styles: tuple[str, ...] = ()
```

- [ ] **Step 4: Implement the resolver**

Add to `src/tf2_loadout/items_game.py`. Import `ItemAttrs` at the top:

```python
from tf2_loadout.models import ItemAttrs
```

Then append:

```python
def _flatten_with_prefabs(node: dict, prefabs: dict, depth: int = 0) -> dict:
    """Merge a node with everything it inherits, own keys winning.

    Prefabs are applied left to right, so a later prefab in the ``prefab`` list beats
    an earlier one, and the node's own keys beat all of them. Merging is shallow: a
    block the node declares replaces the inherited block outright rather than being
    merged key by key, which matches how Valve's tooling treats these overrides.

    Deliberately NOT used for equip regions -- see ``_resolve``, which must not
    inherit once a node declares any region of its own.
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

    The ``styles`` block is keyed by style index; each value is normally a block with
    a ``name``. Unknown shapes yield no styles rather than raising.
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
    paintable = (
        isinstance(capabilities, dict) and capabilities.get("paintable") == "1"
    )
    restriction = node.get("holiday_restriction")
    return ItemAttrs(
        paintable=paintable,
        holiday_restriction=restriction if isinstance(restriction, str) else None,
        styles=_style_names(node),
    )


_NO_ATTRS = ItemAttrs()


def resolve_item_attrs(items_game: dict) -> dict[int, ItemAttrs]:
    """Map each item's defindex to its resolved filterable attributes.

    Items whose attributes are all defaults are omitted, keeping the cache small --
    most of the schema is unpainted, unrestricted and style-less.
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_items_game.py -v`
Expected: PASS, 7 tests (2 pre-existing region/matrix tests plus the 5 new ones)

- [ ] **Step 6: Commit**

```bash
git add src/tf2_loadout/models.py src/tf2_loadout/items_game.py tests/test_items_game.py
git commit -m "feat: resolve paintable, holiday, and style attributes from items_game"
```

---

### Task 2: Carry the new attributes onto Cosmetic

**Files:**
- Modify: `src/tf2_loadout/models.py`
- Modify: `src/tf2_loadout/catalog.py:44-70` (`merge_catalog`)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `ItemAttrs` from Task 1.
- Produces:
  - `Cosmetic` gains `paintable: bool = False`, `holiday_restriction: str | None = None`, `styles: tuple[str, ...] = ()`.
  - `merge_catalog(schema_items: list[dict], equip_regions: dict[int, frozenset[str]], attrs: dict[int, ItemAttrs] | None = None) -> list[Cosmetic]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog.py`:

```python
def test_merge_attaches_item_attrs_when_present():
    regions = {116: frozenset({"hat"})}
    attrs = {
        116: ItemAttrs(
            paintable=True,
            holiday_restriction="halloween_or_fullmoon",
            styles=("Default", "Rogue"),
        )
    }

    [cosmetic] = merge_catalog([SCHEMA_HAT], regions, attrs)

    assert cosmetic.paintable is True
    assert cosmetic.holiday_restriction == "halloween_or_fullmoon"
    assert cosmetic.styles == ("Default", "Rogue")


def test_merge_defaults_attrs_for_items_with_none():
    # Most of the schema has no paint/holiday/style data at all.
    regions = {116: frozenset({"hat"})}

    [cosmetic] = merge_catalog([SCHEMA_HAT], regions, {})

    assert cosmetic.paintable is False
    assert cosmetic.holiday_restriction is None
    assert cosmetic.styles == ()
```

Update the import at the top of `tests/test_catalog.py`:

```python
from tf2_loadout.catalog import parse_schema_items, merge_catalog
from tf2_loadout.models import ItemAttrs
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL — `AttributeError: 'Cosmetic' object has no attribute 'paintable'`

- [ ] **Step 3: Extend Cosmetic and merge_catalog**

In `src/tf2_loadout/models.py`, add three fields to `Cosmetic`, after `image_url`:

```python
    paintable: bool = False
    holiday_restriction: str | None = None
    styles: tuple[str, ...] = ()
```

In `src/tf2_loadout/catalog.py`, import `ItemAttrs`:

```python
from tf2_loadout.models import Cosmetic, ItemAttrs
```

Then replace `merge_catalog` entirely:

```python
_NO_ATTRS = ItemAttrs()


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
                styles=item_attrs.styles,
            )
        )
    return cosmetics
```

- [ ] **Step 4: Run the full backend suite to verify nothing regressed**

Run: `uv run pytest`
Expected: PASS — the new fields default, so existing callers of `merge_catalog` (which pass two arguments) still work.

- [ ] **Step 5: Commit**

```bash
git add src/tf2_loadout/models.py src/tf2_loadout/catalog.py tests/test_catalog.py
git commit -m "feat: carry paintable, holiday, and style attributes onto Cosmetic"
```

---

### Task 3: Version the catalog cache and store the new attributes

**Files:**
- Modify: `src/tf2_loadout/catalog.py:84-105` (`save_catalog_cache`), `:128-154` (`build_from_sources`, `from_cache`)
- Test: `tests/test_catalog_service.py`

**Interfaces:**
- Consumes: `resolve_item_attrs` (Task 1), `merge_catalog(..., attrs)` (Task 2).
- Produces:
  - `tf2_loadout.catalog.CACHE_VERSION: int = 2`
  - `tf2_loadout.catalog.StaleCacheError(RuntimeError)`
  - `CatalogService.conflict_matrix` property returning `dict[str, frozenset[str]]`.
  - `equip.json` gains `"version": 2` and `"attrs": {"<defindex>": {"paintable": bool, "holiday_restriction": str | None, "styles": [str]}}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog_service.py`:

```python
import json

import pytest

from tf2_loadout.catalog import (
    CACHE_VERSION,
    CatalogService,
    StaleCacheError,
    save_catalog_cache,
)

# Minimal items_game/schema pair: one paintable, style-bearing hat.
CACHE_ITEMS_GAME = {
    "equip_conflicts": {"whole_head": {"hat": "1"}},
    "prefabs": {},
    "items": {
        "116": {
            "name": "Modest Pile of Hat",
            "equip_region": "hat",
            "capabilities": {"paintable": "1"},
            "styles": {"0": {"name": "Default"}},
        }
    },
}
CACHE_SCHEMA_ITEMS = [
    {
        "defindex": 116,
        "item_class": "tf_wearable",
        "item_name": "The Modest Pile of Hat",
        "used_by_classes": ["Scout"],
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_catalog_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'CACHE_VERSION'`

- [ ] **Step 3: Implement versioning, attrs persistence, and the matrix property**

In `src/tf2_loadout/catalog.py`, update the imports:

```python
from tf2_loadout.items_game import (
    parse_conflict_matrix,
    resolve_equip_regions,
    resolve_item_attrs,
)
```

Replace the cache constants block:

```python
SCHEMA_CACHE = "schema_items.json"
EQUIP_CACHE = "equip.json"

# Bump whenever equip.json gains or changes a field. A cache written by an older
# version would otherwise load fine and quietly report every item as unpaintable,
# style-less and unrestricted -- wrong answers are worse than a loud failure.
CACHE_VERSION = 2


class StaleCacheError(RuntimeError):
    """Raised when the on-disk cache predates the current cache format."""
```

Replace `save_catalog_cache`:

```python
def save_catalog_cache(
    schema_items: list[dict], items_game: dict, cache_dir: str | Path
) -> None:
    """Cache everything needed to rebuild the catalog offline.

    Stores raw GetSchemaItems plus the *derived* equip data (regions, conflict matrix
    and filterable attributes) -- far smaller than the full items_game.txt.
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
                "styles": list(a.styles),
            }
            for di, a in resolve_item_attrs(items_game).items()
        },
    }
    (cache_dir / EQUIP_CACHE).write_text(json.dumps(equip), encoding="utf-8")
```

Replace `build_from_sources`:

```python
    @classmethod
    def build_from_sources(
        cls, schema_items: list[dict], items_game: dict
    ) -> "CatalogService":
        """Build the catalog by merging GetSchemaItems with items_game equip data."""
        regions = resolve_equip_regions(items_game)
        matrix = parse_conflict_matrix(items_game)
        attrs = resolve_item_attrs(items_game)
        return cls(
            merge_catalog(schema_items, regions, attrs), conflict_matrix=matrix
        )
```

Replace `from_cache`:

```python
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
                f"catalog cache is v{version}, expected v{CACHE_VERSION} -- "
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
                styles=tuple(a["styles"]),
            )
            for di, a in equip["attrs"].items()
        }
        return cls(
            merge_catalog(schema_items, regions, attrs), conflict_matrix=matrix
        )
```

Add the matrix property to `CatalogService`, directly after `__len__`:

```python
    @property
    def conflict_matrix(self) -> dict[str, frozenset[str]]:
        """The cross-region conflict matrix, for shipping to clients."""
        return dict(self._conflict_matrix)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_catalog_service.py -v`
Expected: PASS, including the three new tests

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tf2_loadout/catalog.py tests/test_catalog_service.py
git commit -m "feat: version the catalog cache and persist item attributes"
```

---

### Task 4: Expose the attributes, the conflict matrix, and an unlimited list over HTTP

**Files:**
- Modify: `src/tf2_loadout/api.py:36-44` (`CosmeticOut`), `:88-115` (`to_out`, `list_cosmetics`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Cosmetic.paintable / .holiday_restriction / .styles` (Task 2), `CatalogService.conflict_matrix` (Task 3).
- Produces:
  - `CosmeticOut` gains `paintable: bool`, `holiday_restriction: str | None`, `styles: list[str]`.
  - `GET /equip-conflicts` → `{"matrix": {"<region>": ["<region>", ...]}}`, each list sorted.
  - `GET /cosmetics?limit=0` returns every match, no truncation.

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py` has no pytest fixture — it builds a `TestClient` through a module-level
`_client()` helper whose catalog is three `Cosmetic`s with no conflict matrix. Append these
tests, including the second helper for the matrix endpoint:

```python
def _matrix_client() -> TestClient:
    """A client whose catalog carries a conflict matrix and a paintable, styled item."""
    catalog = CatalogService(
        [
            Cosmetic(
                1,
                "Spy Fedora",
                frozenset({"hat"}),
                ("Spy",),
                "misc",
                "img1",
                paintable=True,
                styles=("Default", "Rogue"),
            ),
            Cosmetic(2, "Spy Mask", frozenset({"whole_head"}), ("Spy",), "misc", "img2"),
        ],
        conflict_matrix={
            "whole_head": frozenset({"hat"}),
            "hat": frozenset({"whole_head"}),
        },
    )
    return TestClient(create_app(catalog, PricingService({})))


def test_cosmetic_payload_includes_filter_attributes():
    r = _matrix_client().get("/cosmetics")

    assert r.status_code == 200
    items = {c["defindex"]: c for c in r.json()["items"]}
    assert items[1]["paintable"] is True
    assert items[1]["styles"] == ["Default", "Rogue"]
    assert items[1]["holiday_restriction"] is None
    assert items[2]["paintable"] is False
    assert items[2]["styles"] == []


def test_equip_conflicts_returns_a_sorted_symmetric_matrix():
    r = _matrix_client().get("/equip-conflicts")

    assert r.status_code == 200
    matrix = r.json()["matrix"]
    assert matrix == {"hat": ["whole_head"], "whole_head": ["hat"]}


def test_limit_zero_returns_everything():
    client = _client()

    everything = client.get("/cosmetics", params={"limit": 0}).json()["items"]
    capped = client.get("/cosmetics", params={"limit": 1}).json()["items"]

    assert len(capped) == 1
    assert len(everything) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `TypeError` on the `paintable=` keyword to `Cosmetic` (Task 2 adds it, so
this fails only if Task 2 was skipped), then `KeyError: 'paintable'` and a 404 for
`/equip-conflicts`

- [ ] **Step 3: Implement the API changes**

In `src/tf2_loadout/api.py`, add three fields to `CosmeticOut` after `image_url`:

```python
    paintable: bool
    holiday_restriction: str | None
    styles: list[str]
```

In `to_out`, pass them through — add these three arguments to the `CosmeticOut(...)` call,
after `image_url=cosmetic.image_url,`:

```python
            paintable=cosmetic.paintable,
            holiday_restriction=cosmetic.holiday_restriction,
            styles=list(cosmetic.styles),
```

Replace `list_cosmetics`:

```python
    @app.get("/cosmetics")
    def list_cosmetics(
        used_by: str | None = None, q: str | None = None, limit: int = 100
    ) -> dict:
        """List cosmetics. ``limit=0`` means no limit -- the browser filters client-side
        and needs the whole class list, not a truncated page of it."""
        items = catalog.for_class(used_by) if used_by else catalog.all()
        if q:
            needle = q.lower()
            items = [c for c in items if needle in c.name.lower()]
        if limit > 0:
            items = items[:limit]
        return {"items": [to_out(c) for c in items]}
```

Add the new endpoint directly after `list_cosmetics`:

```python
    @app.get("/equip-conflicts")
    def equip_conflicts() -> dict:
        """The cross-region conflict matrix.

        Static and small, so the client fetches it once and evaluates clashes locally
        rather than round-tripping on every filter toggle.
        """
        return {
            "matrix": {
                region: sorted(others)
                for region, others in catalog.conflict_matrix.items()
            }
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tf2_loadout/api.py tests/test_api.py
git commit -m "feat: serve item attributes, the conflict matrix, and unlimited lists"
```

---

### Task 5: Set up Vitest and port the conflict rule to TypeScript

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/conflicts.ts`
- Test: `frontend/src/conflicts.test.ts`

**Interfaces:**
- Consumes: the matrix shape from `GET /equip-conflicts` (Task 4).
- Produces:
  - `export type ConflictMatrix = Record<string, string[]>`
  - `export function clashingRegions(a: string[], b: string[], matrix: ConflictMatrix): string[]` — sorted, deduplicated.
  - `export function clashes(a: string[], b: string[], matrix: ConflictMatrix): boolean`

- [ ] **Step 1: Install Vitest**

Run: `cd frontend && pnpm add -D vitest`

Then add a `test` script to `frontend/package.json`, inside `"scripts"`, after `"lint"`:

```json
    "test": "vitest run"
```

Both modules under test are pure functions, so no DOM environment or config file is needed —
Vitest's defaults are enough.

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/conflicts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { clashes, clashingRegions, type ConflictMatrix } from "./conflicts";

// Mirrors tests/test_conflicts.py: the Python implementation is the source of truth.
const MATRIX: ConflictMatrix = {
  whole_head: ["hat", "face", "glasses"],
  hat: ["whole_head"],
  face: ["whole_head", "glasses"],
  glasses: ["whole_head", "face"],
};

describe("clashingRegions", () => {
  it("reports identical regions as clashing", () => {
    expect(clashingRegions(["hat"], ["hat"], {})).toEqual(["hat"]);
  });

  it("reports cross-region clashes from the matrix", () => {
    expect(clashingRegions(["whole_head"], ["hat"], MATRIX)).toEqual([
      "hat",
      "whole_head",
    ]);
  });

  it("reports a clash when only the reverse direction is listed", () => {
    // Half-populated matrix: the server sends a symmetric one, but the rule must
    // not depend on that.
    expect(clashingRegions(["hat"], ["whole_head"], { whole_head: ["hat"] })).toEqual(
      ["hat", "whole_head"]
    );
  });

  it("returns nothing for unrelated regions", () => {
    expect(clashingRegions(["hat"], ["feet"], MATRIX)).toEqual([]);
  });

  it("deduplicates when several region pairs clash", () => {
    expect(clashingRegions(["whole_head"], ["hat", "face"], MATRIX)).toEqual([
      "face",
      "hat",
      "whole_head",
    ]);
  });
});

describe("clashes", () => {
  it("is true when any region pair conflicts", () => {
    expect(clashes(["whole_head"], ["hat"], MATRIX)).toBe(true);
  });

  it("is false for disjoint, unrelated regions", () => {
    expect(clashes(["hat"], ["feet"], MATRIX)).toBe(false);
  });

  it("is false when either side has no regions", () => {
    expect(clashes([], ["hat"], MATRIX)).toBe(false);
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && pnpm test`
Expected: FAIL — cannot resolve `./conflicts`

- [ ] **Step 4: Implement the port**

Create `frontend/src/conflicts.ts`:

```ts
/**
 * Equip-region conflict rule, ported from `src/tf2_loadout/conflicts.py`.
 *
 * The browser needs this locally so filter toggles are instant instead of a round-trip
 * per keystroke. Python stays the source of truth: `conflicts.test.ts` mirrors
 * `tests/test_conflicts.py`, so a change to the rule there should fail here too.
 */

/** Region -> regions it conflicts with. The server sends this symmetric. */
export type ConflictMatrix = Record<string, string[]>;

/**
 * Regions involved in a clash between two items, sorted and deduplicated.
 *
 * Two regions conflict when they are identical, or when either is listed against the
 * other in the matrix. The reverse-direction check is not redundant defensiveness: the
 * rule must hold for a half-populated matrix, as the Python version's does.
 */
export function clashingRegions(
  a: string[],
  b: string[],
  matrix: ConflictMatrix
): string[] {
  const out = new Set<string>();
  for (const ra of a) {
    for (const rb of b) {
      const conflicts =
        ra === rb ||
        (matrix[ra]?.includes(rb) ?? false) ||
        (matrix[rb]?.includes(ra) ?? false);
      if (conflicts) {
        out.add(ra);
        out.add(rb);
      }
    }
  }
  return [...out].sort();
}

/** Whether two items can be worn together. */
export function clashes(
  a: string[],
  b: string[],
  matrix: ConflictMatrix
): boolean {
  return clashingRegions(a, b, matrix).length > 0;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && pnpm test`
Expected: PASS, 8 tests

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/conflicts.ts frontend/src/conflicts.test.ts
git commit -m "feat: port the equip-conflict rule to the client, with Vitest"
```

---

### Task 6: Implement the filter pipeline

**Files:**
- Create: `frontend/src/filters.ts`
- Test: `frontend/src/filters.test.ts`

**Interfaces:**
- Consumes: `clashingRegions`, `ConflictMatrix` (Task 5); the `Cosmetic` type from `./api`.
- Produces:
  - `export type Scope = "any" | "one" | "multi" | "all"`
  - `export type SortKey = "index" | "name"`
  - `export interface FilterState { scope: Scope; sort: SortKey; desc: boolean; noClashes: boolean; equippedOnly: boolean; paintable: boolean; hasStyles: boolean; hideHalloween: boolean }`
  - `export const DEFAULT_FILTERS: FilterState`
  - `export interface GridEntry { item: Cosmetic; dimmed: boolean; clashesWith: string[] }` — `clashesWith` holds the *names* of tray items it fights.
  - `export function applyFilters(items: Cosmetic[], state: FilterState, tray: Cosmetic[], matrix: ConflictMatrix): GridEntry[]`
  - `export function activeFilterCount(state: FilterState): number`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/filters.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, applyFilters, activeFilterCount } from "./filters";
import type { ConflictMatrix } from "./conflicts";
import type { Cosmetic } from "./api";

const MATRIX: ConflictMatrix = { whole_head: ["hat"], hat: ["whole_head"] };

function cosmetic(over: Partial<Cosmetic> & { defindex: number }): Cosmetic {
  return {
    name: `Item ${over.defindex}`,
    equip_regions: ["hat"],
    used_by_classes: ["Spy"],
    item_slot: "head",
    image_url: null,
    price: null,
    paintable: false,
    holiday_restriction: null,
    styles: [],
    ...over,
  };
}

const FEDORA = cosmetic({ defindex: 1, name: "Fancy Fedora" });
const MASK = cosmetic({ defindex: 2, name: "Whole Head", equip_regions: ["whole_head"] });
const BOOTS = cosmetic({ defindex: 3, name: "Boots", equip_regions: ["feet"] });
const PAINTED = cosmetic({ defindex: 4, name: "Painted", paintable: true });
const SPOOKY = cosmetic({
  defindex: 5,
  name: "Spooky",
  holiday_restriction: "halloween_or_fullmoon",
});
const STYLED = cosmetic({ defindex: 6, name: "Styled", styles: ["Default", "Rogue"] });
const ALL_CLASS = cosmetic({
  defindex: 7,
  name: "All Class",
  used_by_classes: [
    "Scout", "Soldier", "Pyro", "Demoman", "Heavy",
    "Engineer", "Medic", "Sniper", "Spy",
  ],
});
const TWO_CLASS = cosmetic({
  defindex: 8,
  name: "Two Class",
  used_by_classes: ["Spy", "Scout"],
});

const ALL = [FEDORA, MASK, BOOTS, PAINTED, SPOOKY, STYLED, ALL_CLASS, TWO_CLASS];

function names(entries: { item: Cosmetic }[]): string[] {
  return entries.map((e) => e.item.name);
}

describe("applyFilters defaults", () => {
  it("returns everything undimmed with an empty tray", () => {
    const out = applyFilters(ALL, DEFAULT_FILTERS, [], MATRIX);

    expect(out).toHaveLength(ALL.length);
    expect(out.every((e) => !e.dimmed)).toBe(true);
  });
});

describe("clash dimming", () => {
  it("dims items that clash with the tray and names the offender", () => {
    const state = { ...DEFAULT_FILTERS, noClashes: true };

    const out = applyFilters(ALL, state, [FEDORA], MATRIX);
    const mask = out.find((e) => e.item.defindex === MASK.defindex)!;
    const boots = out.find((e) => e.item.defindex === BOOTS.defindex)!;

    expect(mask.dimmed).toBe(true);
    expect(mask.clashesWith).toEqual(["Fancy Fedora"]);
    expect(boots.dimmed).toBe(false);
  });

  it("never dims an item against itself", () => {
    const state = { ...DEFAULT_FILTERS, noClashes: true };

    const out = applyFilters(ALL, state, [FEDORA], MATRIX);
    const fedora = out.find((e) => e.item.defindex === FEDORA.defindex)!;

    expect(fedora.dimmed).toBe(false);
    expect(fedora.clashesWith).toEqual([]);
  });

  it("hides nothing -- dimmed items stay in the grid", () => {
    const state = { ...DEFAULT_FILTERS, noClashes: true };

    expect(applyFilters(ALL, state, [FEDORA], MATRIX)).toHaveLength(ALL.length);
  });

  it("dims nothing when the toggle is off", () => {
    const out = applyFilters(ALL, DEFAULT_FILTERS, [FEDORA], MATRIX);

    expect(out.every((e) => !e.dimmed)).toBe(true);
  });
});

describe("attribute filters", () => {
  it("keeps only paintable items", () => {
    const state = { ...DEFAULT_FILTERS, paintable: true };

    expect(names(applyFilters(ALL, state, [], MATRIX))).toEqual(["Painted"]);
  });

  it("keeps only items with styles", () => {
    const state = { ...DEFAULT_FILTERS, hasStyles: true };

    expect(names(applyFilters(ALL, state, [], MATRIX))).toEqual(["Styled"]);
  });

  it("drops holiday-restricted items", () => {
    const state = { ...DEFAULT_FILTERS, hideHalloween: true };

    expect(names(applyFilters(ALL, state, [], MATRIX))).not.toContain("Spooky");
  });

  it("keeps only tray items when equippedOnly is set", () => {
    const state = { ...DEFAULT_FILTERS, equippedOnly: true };

    expect(names(applyFilters(ALL, state, [FEDORA, BOOTS], MATRIX))).toEqual([
      "Fancy Fedora",
      "Boots",
    ]);
  });
});

describe("class scope", () => {
  it("one: keeps items wearable by exactly one class", () => {
    const state = { ...DEFAULT_FILTERS, scope: "one" as const };

    expect(names(applyFilters(ALL, state, [], MATRIX))).not.toContain("All Class");
    expect(names(applyFilters(ALL, state, [], MATRIX))).not.toContain("Two Class");
  });

  it("multi: keeps items wearable by two to eight classes", () => {
    const state = { ...DEFAULT_FILTERS, scope: "multi" as const };

    expect(names(applyFilters(ALL, state, [], MATRIX))).toEqual(["Two Class"]);
  });

  it("all: keeps only all-class items", () => {
    const state = { ...DEFAULT_FILTERS, scope: "all" as const };

    expect(names(applyFilters(ALL, state, [], MATRIX))).toEqual(["All Class"]);
  });
});

describe("sorting", () => {
  it("sorts by name ascending by default", () => {
    const state = { ...DEFAULT_FILTERS, sort: "name" as const };

    expect(names(applyFilters(ALL, state, [], MATRIX))[0]).toBe("All Class");
  });

  it("reverses when desc is set", () => {
    const asc = { ...DEFAULT_FILTERS, sort: "name" as const };
    const desc = { ...asc, desc: true };

    expect(names(applyFilters(ALL, desc, [], MATRIX))).toEqual(
      names(applyFilters(ALL, asc, [], MATRIX)).reverse()
    );
  });

  it("sorts by defindex when sort is index", () => {
    const state = { ...DEFAULT_FILTERS, sort: "index" as const };

    const out = applyFilters(ALL, state, [], MATRIX);
    expect(out.map((e) => e.item.defindex)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });
});

describe("activeFilterCount", () => {
  it("is zero for the defaults", () => {
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0);
  });

  it("counts each engaged toggle and a non-default scope", () => {
    const state = {
      ...DEFAULT_FILTERS,
      scope: "all" as const,
      paintable: true,
      hideHalloween: true,
    };

    expect(activeFilterCount(state)).toBe(3);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && pnpm test`
Expected: FAIL — cannot resolve `./filters`

- [ ] **Step 3: Implement the pipeline**

Create `frontend/src/filters.ts`:

```ts
/**
 * Browser filtering, sorting, and clash dimming.
 *
 * Pure functions with no React dependency, so the whole grid pipeline is testable
 * without a DOM. `App.tsx` owns the state; this module owns the rules.
 */

import { clashingRegions, type ConflictMatrix } from "./conflicts";
import type { Cosmetic } from "./api";

/** How exclusive an item is, within the selected class's items. */
export type Scope = "any" | "one" | "multi" | "all";

export type SortKey = "index" | "name";

export interface FilterState {
  scope: Scope;
  sort: SortKey;
  desc: boolean;
  /** Dim (never hide) items that clash with the tray. */
  noClashes: boolean;
  equippedOnly: boolean;
  paintable: boolean;
  hasStyles: boolean;
  hideHalloween: boolean;
}

export const DEFAULT_FILTERS: FilterState = {
  scope: "any",
  sort: "index",
  desc: false,
  noClashes: false,
  equippedOnly: false,
  paintable: false,
  hasStyles: false,
  hideHalloween: false,
};

export interface GridEntry {
  item: Cosmetic;
  dimmed: boolean;
  /** Names of the tray items this one fights, for the badge. */
  clashesWith: string[];
}

const ALL_CLASSES = 9;

function matchesScope(item: Cosmetic, scope: Scope): boolean {
  const count = item.used_by_classes.length;
  switch (scope) {
    case "one":
      return count === 1;
    case "multi":
      return count > 1 && count < ALL_CLASSES;
    case "all":
      return count >= ALL_CLASSES;
    default:
      return true;
  }
}

function compare(a: Cosmetic, b: Cosmetic, sort: SortKey): number {
  if (sort === "name") return a.name.localeCompare(b.name);
  return a.defindex - b.defindex;
}

/**
 * Filter, sort, and annotate the grid.
 *
 * Clashing items are dimmed rather than removed: TF2's equip-region rules are opaque
 * enough that an item silently vanishing is worse than one that explains itself.
 */
export function applyFilters(
  items: Cosmetic[],
  state: FilterState,
  tray: Cosmetic[],
  matrix: ConflictMatrix
): GridEntry[] {
  const equipped = new Set(tray.map((c) => c.defindex));

  const kept = items.filter((item) => {
    if (!matchesScope(item, state.scope)) return false;
    if (state.equippedOnly && !equipped.has(item.defindex)) return false;
    if (state.paintable && !item.paintable) return false;
    if (state.hasStyles && item.styles.length === 0) return false;
    if (state.hideHalloween && item.holiday_restriction !== null) return false;
    return true;
  });

  const sorted = [...kept].sort((a, b) => compare(a, b, state.sort));
  if (state.desc) sorted.reverse();

  return sorted.map((item) => {
    const clashesWith = state.noClashes
      ? tray
          .filter(
            (worn) =>
              worn.defindex !== item.defindex &&
              clashingRegions(item.equip_regions, worn.equip_regions, matrix)
                .length > 0
          )
          .map((worn) => worn.name)
      : [];
    return { item, dimmed: clashesWith.length > 0, clashesWith };
  });
}

/** How many filters are engaged, for the "clear" affordance. */
export function activeFilterCount(state: FilterState): number {
  return [
    state.scope !== DEFAULT_FILTERS.scope,
    state.noClashes,
    state.equippedOnly,
    state.paintable,
    state.hasStyles,
    state.hideHalloween,
  ].filter(Boolean).length;
}
```

- [ ] **Step 4: Add the new fields to the Cosmetic type**

The tests will not compile until `Cosmetic` carries the attributes. In
`frontend/src/api.ts`, add three fields to the `Cosmetic` interface after `price`:

```ts
  paintable: boolean;
  holiday_restriction: string | null;
  styles: string[];
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && pnpm test`
Expected: PASS, all conflict and filter tests

- [ ] **Step 6: Commit**

```bash
git add frontend/src/filters.ts frontend/src/filters.test.ts frontend/src/api.ts
git commit -m "feat: add the client-side filter and sort pipeline"
```

---

### Task 7: Render the filter bar and wire it into the bench

**Files:**
- Create: `frontend/src/FilterBar.tsx`
- Modify: `frontend/src/api.ts` (add `fetchConflictMatrix`, drop the hard-coded limit)
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `applyFilters`, `DEFAULT_FILTERS`, `FilterState`, `GridEntry`, `activeFilterCount` (Task 6); `ConflictMatrix` (Task 5); `GET /equip-conflicts` and `limit=0` (Task 4).
- Produces: `export default function FilterBar({ state, onChange }: { state: FilterState; onChange: (next: FilterState) => void })`.

- [ ] **Step 1: Add the matrix fetch and remove the list cap**

In `frontend/src/api.ts`, import the matrix type at the top:

```ts
import type { ConflictMatrix } from "./conflicts";
```

Replace `fetchCosmetics` — the browser now filters locally, so it needs the whole class list:

```ts
export async function fetchCosmetics(usedBy: string, q: string): Promise<Cosmetic[]> {
  // limit=0 means "everything": filtering happens client-side, so a truncated page
  // would silently hide items the user has filtered down to.
  const params = new URLSearchParams({ used_by: usedBy, limit: "0" });
  if (q) params.set("q", q);
  const res = await fetch(`${BASE}/cosmetics?${params}`);
  if (!res.ok) throw new Error(`cosmetics ${res.status}`);
  return (await res.json()).items;
}
```

Add, directly after it:

```ts
/** The cross-region conflict matrix. Static, so fetch once and keep it. */
export async function fetchConflictMatrix(): Promise<ConflictMatrix> {
  const res = await fetch(`${BASE}/equip-conflicts`);
  if (!res.ok) throw new Error(`equip-conflicts ${res.status}`);
  return (await res.json()).matrix;
}
```

- [ ] **Step 2: Write the FilterBar component**

Create `frontend/src/FilterBar.tsx`:

```tsx
import type { FilterState, Scope, SortKey } from "./filters";
import { DEFAULT_FILTERS, activeFilterCount } from "./filters";

const SCOPES: { value: Scope; label: string; title: string }[] = [
  { value: "any", label: "Any", title: "Every cosmetic this class can wear" },
  { value: "one", label: "One", title: "Wearable by this class only" },
  { value: "multi", label: "Multi", title: "Wearable by 2 to 8 classes" },
  { value: "all", label: "All", title: "Wearable by all nine classes" },
];

const TOGGLES: { key: keyof FilterState; label: string; title: string }[] = [
  {
    key: "noClashes",
    label: "No clashes",
    title: "Dim cosmetics that conflict with your loadout",
  },
  { key: "equippedOnly", label: "Equipped only", title: "Show only what you're wearing" },
  { key: "paintable", label: "Paintable", title: "Only cosmetics that accept paint" },
  { key: "hasStyles", label: "Has styles", title: "Only cosmetics with style variants" },
  {
    key: "hideHalloween",
    label: "Hide Halloween",
    title: "Hide items only wearable during Halloween or a full moon",
  },
];

export default function FilterBar({
  state,
  onChange,
}: {
  state: FilterState;
  onChange: (next: FilterState) => void;
}) {
  const active = activeFilterCount(state);

  return (
    <div className="filterbar">
      <div className="filter-row">
        <span className="filter-label">Scope</span>
        <div className="segmented" role="group" aria-label="Class scope">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              type="button"
              title={s.title}
              aria-pressed={state.scope === s.value}
              onClick={() => onChange({ ...state, scope: s.value })}
            >
              {s.label}
            </button>
          ))}
        </div>

        <span className="filter-label">Sort</span>
        <select
          aria-label="Sort by"
          value={state.sort}
          onChange={(e) => onChange({ ...state, sort: e.target.value as SortKey })}
        >
          <option value="index">Schema order</option>
          <option value="name">Name</option>
        </select>
        <button
          type="button"
          className="sort-dir"
          aria-pressed={state.desc}
          title={state.desc ? "Descending" : "Ascending"}
          onClick={() => onChange({ ...state, desc: !state.desc })}
        >
          {state.desc ? "↓" : "↑"}
        </button>
      </div>

      <div className="filter-row">
        {TOGGLES.map((t) => (
          <button
            key={t.key}
            type="button"
            className="filter-chip"
            title={t.title}
            aria-pressed={Boolean(state[t.key])}
            onClick={() => onChange({ ...state, [t.key]: !state[t.key] })}
          >
            {t.label}
          </button>
        ))}
        {active > 0 && (
          <button
            type="button"
            className="filter-clear"
            onClick={() => onChange(DEFAULT_FILTERS)}
          >
            Clear {active}
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire it into App.tsx**

In `frontend/src/App.tsx`, extend the imports:

```tsx
import {
  fetchCosmetics,
  fetchCosmetic,
  fetchConflicts,
  fetchConflictMatrix,
  fetchChatAvailable,
  formatPrice,
  backpackUrl,
  type Cosmetic,
  type Conflict,
} from "./api";
import { DEFAULT_FILTERS, applyFilters, type FilterState } from "./filters";
import type { ConflictMatrix } from "./conflicts";
import FilterBar from "./FilterBar";
```

Add two pieces of state, directly after the `const [conflicts, setConflicts] = ...` line:

```tsx
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [matrix, setMatrix] = useState<ConflictMatrix>({});
```

Fetch the matrix once. Add this effect directly after the `fetchChatAvailable` effect:

```tsx
  // The conflict matrix is static, so fetch it once and filter locally afterwards.
  // An empty matrix still detects same-region clashes, so a failure degrades rather
  // than breaks.
  useEffect(() => {
    fetchConflictMatrix()
      .then(setMatrix)
      .catch(() => setMatrix({}));
  }, []);
```

Add the derived grid, directly after the `equippedIds` memo:

```tsx
  const entries = useMemo(
    () => applyFilters(cosmetics, filters, loadout, matrix),
    [cosmetics, filters, loadout, matrix]
  );
```

Render the bar. Directly after the closing `</div>` of the `toolbar` div, add:

```tsx
          <FilterBar state={filters} onChange={setFilters} />
```

Replace the grid block. The existing markup maps over `cosmetics`; it must map over
`entries` instead so dimming and badges reach the DOM. Replace everything from
`<div className="grid">` to its closing `</div>` with:

```tsx
            <div className="grid">
              {entries.map(({ item: c, dimmed, clashesWith }) => (
                <div
                  key={c.defindex}
                  className={[
                    "cell",
                    equippedIds.has(c.defindex) ? "selected" : "",
                    dimmed ? "dimmed" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  role="button"
                  tabIndex={0}
                  aria-pressed={equippedIds.has(c.defindex)}
                  onClick={() => equip(c)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      equip(c);
                    }
                  }}
                  title={equippedIds.has(c.defindex) ? "Remove from loadout" : "Add to loadout"}
                >
                  <div className="thumb">
                    {equippedIds.has(c.defindex) && <span className="equipped-tick">✓</span>}
                    {c.image_url && <img src={c.image_url} alt="" loading="lazy" />}
                  </div>
                  <div className="name">{c.name}</div>
                  {clashesWith.length > 0 && (
                    <div className="clash-badge">clashes with {clashesWith.join(", ")}</div>
                  )}
                  <div className="regions">
                    {c.equip_regions.map((r) => (
                      <span key={r} className="region">
                        {r}
                      </span>
                    ))}
                  </div>
                  <div className="meta">
                    <span className={c.price ? "price" : "price none"}>
                      {c.price ? formatPrice(c.price) : "unpriced"}
                    </span>
                    <a
                      className="bptf"
                      href={backpackUrl(c.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`${c.name} on backpack.tf`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      backpack.tf ↗
                    </a>
                  </div>
                </div>
              ))}
            </div>
```

Finally, the "no results" case. The existing `status` message only covers an empty
*fetch*; filters can now empty the grid with a non-empty fetch. Replace the status
ternary's condition so it reads:

```tsx
          {status ? (
            <p className="status">{status}</p>
          ) : entries.length === 0 ? (
            <p className="status">No cosmetics match these filters.</p>
          ) : (
```

- [ ] **Step 4: Style the bar**

Append to `frontend/src/index.css`:

```css
/* --- filter bar --- */
.filterbar {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin: 0.5rem 0 0.9rem;
}
.filter-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.filter-label {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.7;
}
.segmented {
  display: inline-flex;
  border-radius: 4px;
  overflow: hidden;
}
.segmented button,
.filter-chip,
.sort-dir,
.filter-clear {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.25rem 0.6rem;
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0.65;
}
.segmented button {
  border-radius: 0;
  border-right-width: 0;
}
.segmented button:last-child {
  border-right-width: 1px;
}
.filter-chip,
.sort-dir,
.filter-clear {
  border-radius: 999px;
}
.segmented button[aria-pressed="true"],
.filter-chip[aria-pressed="true"],
.sort-dir[aria-pressed="true"] {
  opacity: 1;
  font-weight: 600;
}
.filter-chip:hover,
.segmented button:hover,
.sort-dir:hover,
.filter-clear:hover {
  opacity: 1;
}

/* Dimmed cells stay clickable -- the tray still stamps the conflict. */
.cell.dimmed {
  opacity: 0.38;
}
.cell.dimmed:hover,
.cell.dimmed:focus-within {
  opacity: 0.75;
}
.clash-badge {
  font-size: 0.72rem;
  line-height: 1.2;
  opacity: 0.85;
  margin-top: 0.15rem;
}
```

- [ ] **Step 5: Typecheck, lint, and test**

Run: `cd frontend && pnpm build && pnpm lint && pnpm test`
Expected: build succeeds (`tsc -b` clean), lint clean, tests PASS

- [ ] **Step 6: Verify by hand in the browser**

Terminal 1: `uv run tf2-loadout-api`
Terminal 2: `cd frontend && pnpm dev`, then open http://localhost:5173

If the API exits with a `StaleCacheError`, that is Task 3 working as designed — rebuild the
cache with `uv run pytest --live` (needs `STEAM_API_KEY` in `.env`).

Confirm each of these:
- The class list is no longer capped at 120 items for a large class.
- Scope One / Multi / All each change the grid; Any restores it.
- Sort by name reorders; the arrow reverses it.
- Equip a hat, enable "No clashes": other head items dim and read "clashes with <hat name>".
- Paintable, Has styles, and Hide Halloween each change the count.
- "Clear N" resets everything.
- Filtering everything out shows "No cosmetics match these filters."

- [ ] **Step 7: Commit**

```bash
git add frontend/src/FilterBar.tsx frontend/src/App.tsx frontend/src/api.ts frontend/src/index.css
git commit -m "feat: add the browser filter bar with clash dimming"
```

---

### Task 8: Document the new surfaces

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Update CLAUDE.md**

In the **Architecture** section, append this paragraph after the "Conflicts are not just
same-region overlap" paragraph:

```markdown
**The conflict rule exists twice, on purpose.** `conflicts.py` is the source of truth;
`frontend/src/conflicts.ts` is a deliberate port so filter toggles are instant instead of a
round-trip per click. `conflicts.test.ts` mirrors `tests/test_conflicts.py` — change the
rule in one place and the other's tests should fail. Filtering, sorting, and clash dimming
all live in `frontend/src/filters.ts` as pure functions; `App.tsx` owns only the state.

**The catalog cache is versioned.** `equip.json` carries a `version`, and
`CatalogService.from_cache` raises `StaleCacheError` on a mismatch rather than serving a
catalog where every item silently reads as unpaintable and style-less. There is no refresh
script: rebuild with `uv run pytest --live`. Bump `CACHE_VERSION` whenever the file's shape
changes.
```

In the **Testing** section, append:

```markdown
The frontend has its own suite: `cd frontend && pnpm test` (Vitest). It covers the pure
modules only — `conflicts.ts` and `filters.ts`. There is no DOM environment and no component
test; keep logic out of components so it stays that way.
```

- [ ] **Step 2: Update the README**

Add `cd frontend && pnpm test` to the commands block, next to the existing `pnpm dev` line.
If the README lists features, add a line noting the browser now supports class scope,
sorting, and filters for clashes, paint, styles, and Halloween restrictions.

- [ ] **Step 3: Run everything one last time**

Run: `uv run pytest && cd frontend && pnpm build && pnpm lint && pnpm test`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: cover the filter pipeline, ported conflict rule, and cache versioning"
```

---

## Spec coverage

| Spec requirement | Task |
|---|---|
| Class scope (One / Multi / All) | 6, 7 |
| Sort (index, name, direction) | 6, 7 |
| Equipped only | 6, 7 |
| No clashes → dim + badge | 5, 6, 7 |
| Paintable | 1, 2, 4, 6, 7 |
| Has styles | 1, 2, 4, 6, 7 |
| Hide Halloween-restricted | 1, 2, 4, 6, 7 |
| Prefab-inherited attributes | 1 |
| Cache versioning + loud stale error | 3 |
| `GET /equip-conflicts` | 4 |
| `limit=0` | 4, 7 |
| `conflicts.ts` port with mirrored fixtures | 5 |
| `filters.ts` pure pipeline | 6 |
| Inline filter bar layout | 7 |
| Vitest | 5 |
| Tolerate odd `styles` shapes | 1 |
