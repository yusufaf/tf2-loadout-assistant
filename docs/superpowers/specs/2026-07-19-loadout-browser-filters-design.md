# Loadout Browser Filters — Design

**Date:** 2026-07-19
**Status:** Implemented. See the corrections at the foot of this document — one of this
spec's factual claims turned out to be wrong.

## Goal

Close the browsing-power gap between the loadout bench and [loadout.tf](https://loadout.tf/).
Today the bench offers a class picker and a name search; loadout.tf offers scope, sort, and
half a dozen toggles. Everything here is browsing and filtering — 3D rendering stays out of
scope for v1, as does the weapons/taunts/medals catalog.

## Non-goals

- 3D model rendering, team colours, paint colour preview, Gold/Ice skins.
- Weapons, taunts, medals, or workshop items. The catalog deliberately keeps only
  `tf_wearable` items with resolved equip regions; that filter stays.
- Price-based filters and budget-aware advisor prompts. These are a real differentiator
  (loadout.tf has neither pricing nor an LLM) but belong in a separate spec.

## Filters in scope

| Filter | Control | Data source |
|---|---|---|
| Class scope | Segmented: One / Multi / All | `used_by_classes` (existing) |
| Sort | Dropdown + direction toggle: index, name | `defindex`, `name` (existing) |
| Equipped only | Toggle chip | Client tray state |
| No clashes | Toggle chip | Conflict matrix (new endpoint) |
| Paintable | Toggle chip | `items_game` (new parse) |
| Has styles | Toggle chip | `items_game` (new parse) |
| Hide Halloween-restricted | Toggle chip | `items_game` (new parse) |

### Class scope semantics

The scope control filters the *currently selected class's* items by how exclusive they are.
It does not show items belonging to other classes.

- **One** — wearable by exactly 1 class (class-specific cosmetics).
- **Multi** — wearable by 2 to 8 classes.
- **All** — wearable by all 9 classes (all-class cosmetics).

Default is no scope restriction (show everything for the class).

### Clash behaviour

Items that conflict with something already in the tray are **dimmed and badged**, not hidden.
The badge names the offending tray item ("clashes with Fancy Fedora"). Clicking still equips,
and the tray stamps the conflict as it does today.

Rationale: hiding teaches nothing. TF2's equip-region system is genuinely confusing, and an
item silently vanishing when the tray fills is worse than an item that explains itself. This
diverges from loadout.tf deliberately.

## Architecture

### 1. Data layer

Three attributes join `Cosmetic`, each inherited through the same `prefab` chain that equip
regions already traverse:

- `paintable: bool` — from `capabilities.paintable == "1"`
- `holiday_restriction: str | None` — from `holiday_restriction` (e.g. `halloween_or_fullmoon`)
- `styles: tuple[str, ...]` — style names from the `styles` block

`items_game.py` currently recurses the prefab chain inside `resolve_equip_regions`
(depth-capped at 8). Adding three more resolvers would mean four near-identical recursions.
Extract the traversal once:

```
_flatten_with_prefabs(items_game) -> dict[int, dict]
```

Each resolver then reads a flat, fully-inherited dict per defindex. The depth cap and its
reasoning are unchanged; only the recursion's home moves.

`Cosmetic` gains the three fields with defaults (`False`, `None`, `()`) so existing minimal
test fixtures keep constructing.

`merge_catalog` takes the flattened attrs alongside the regions it already receives.

### 2. Cache migration

`save_catalog_cache` writes derived equip data to `.cache/equip.json`. That file gains an
`attrs` block, and the only way to rebuild it is `uv run pytest --live` with a
`STEAM_API_KEY` present — there is no refresh script.

An existing cache read by new code would report every item as unpaintable, style-less, and
unrestricted: wrong, and silently so. To prevent that, `equip.json` gains a `version` key.
`CatalogService.from_cache` raises a clear error on mismatch:

> cache is stale (v1, expected v2) — rebuild with `uv run pytest --live`

Failing loudly beats serving quietly-wrong filters.

### 3. API

- `CosmeticOut` gains `paintable`, `holiday_restriction`, `styles`.
- New `GET /equip-conflicts` returns the region conflict matrix as `{region: [regions]}`.
  Static and small; the client fetches it once on mount.
- `/cosmetics` accepts `limit=0` meaning "no limit". Client-side filtering needs the full
  class list, and the current `limit=100` default already truncates larger classes silently.

### 4. Frontend

Filtering runs client-side. The frontend holds the full class list plus the conflict matrix,
so every toggle is instant with no round-trip — matching how loadout.tf's toggles feel.

Two new modules, both pure functions with no React dependency:

- **`conflicts.ts`** — `clashes(a, b, matrix)`. A TypeScript port of the pair rule in
  `conflicts.find_conflicts`: two regions conflict if identical **or** listed against each
  other in the matrix. Python remains the source of truth; the port is tested against
  fixtures mirroring `test_conflicts.py`.
- **`filters.ts`** — `applyFilters(items, filterState, tray, matrix)` returning
  `{item, dimmed, clashesWith}[]`. All filter and sort logic lives here.

Then `FilterBar.tsx` renders the controls, and `App.tsx` owns the filter state and wires it
through. `App.tsx` is already 424 lines; keeping the logic in pure modules stops it absorbing
another 150.

### UI layout

An inline filter bar sits directly under the search field — always visible, no hidden state:

```
classbar: Scout Soldier Pyro ... Spy

[ Search Spy cosmetics...              ]

Scope: (One)(Multi)(All)   Sort: [A-Z v] up/dn
[x] No clashes  [ ] Equipped only  [ ] Paintable
[ ] Has styles  [ ] Hide Halloween

+---+ +---+ +---+ +---+ +---+
|hat| |hat| |hat| |hat| |hat|   grid
+---+ +---+ +---+ +---+ +---+
```

Seven controls stay readable in two rows. A collapsible drawer was considered and rejected:
hiding filter state behind a button means the grid can be filtered in ways the user cannot
see.

## Testing

**Python.** Extend `test_items_game.py` with fixtures covering prefab-inherited paintable,
holiday-restriction, and styles values — inheritance is where this parsing will break.
`test_catalog.py` covers the merge and the cache round-trip including the version mismatch
error. `test_api.py` covers `/equip-conflicts` and `limit=0`.

**TypeScript.** `frontend/` has no test runner today. Shipping a hand-ported conflict rule
with no tests is the largest risk in this design, so this work adds **Vitest** as a dev
dependency with a `pnpm test` script, covering `conflicts.ts` and `filters.ts`. Both are pure
functions, so no DOM environment is needed.

The alternative — keeping conflict logic server-side behind a `POST /loadout/clashing`
endpoint — avoids the port entirely but costs a round-trip on every tray change. The port
plus tests is the better trade for responsiveness.

## Risks

- **Port drift.** `conflicts.ts` and `conflicts.py` can diverge as the Python evolves. Shared
  fixtures reduce but do not eliminate this. Accepted: the rule is ~10 lines and has been
  stable.
- **Stale caches in the wild.** Anyone with a pre-existing `.cache` must rerun the live test.
  The version check makes this a clear error rather than a silent wrong answer.
- **`items_game` shape surprises.** Style blocks in particular have several forms across
  items. Parsing should tolerate unknown shapes by yielding no styles rather than raising.

## Corrections found during implementation

**Styles are not in `items_game`.** This spec asserted that style variants are resolved
from a `styles` block in `items_game.txt`, inherited through the prefab chain. They are
not there at all — not on any item, not on any prefab. A live check found zero of 11,498
items carrying a `styles` key, including Team Captain, which visibly has styles in game.

Styles come from `GetSchemaItems`, as a list of `{"name": ...}` objects on the item: 580
wearables, 2,216 style entries, of which only 9 are untranslated localization tokens.
`ItemAttrs` therefore carries only `paintable` and `holiday_restriction`, and
`catalog._styles` reads style names off the raw schema item.

This survived a full fixture-based test suite, because the fixtures encoded the same
wrong assumption as the spec. It was caught only by counting attributes in the rebuilt
cache. Verify schema-parsing work against real data, not just fixtures.

**`resolve_equip_regions` kept its own recursion.** The spec proposed folding it into the
shared prefab flattener. A generic key-level merge would union an item's own
`equip_region` with a prefab's `equip_regions`, where the existing code stops inheriting
once a node declares a region of its own. The flattener serves the new attributes only.

**Live tests never loaded `.env`.** A pre-existing bug, unrelated to this work but
blocking it: only `test_agent_live.py` called `load_env()`, so `pytest --live` hit Steam
and backpack.tf with an empty key and failed 403 even with a valid key on disk. A
session fixture in `conftest.py` now loads `.env` when `--live` is passed, and only then.

**Cache version is 3, not 2.** Moving styles out of the derived cache changed the `attrs`
block shape a second time.
