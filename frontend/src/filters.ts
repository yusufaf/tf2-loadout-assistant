/**
 * Browser filtering, sorting, and clash dimming.
 *
 * Pure functions with no React dependency, so the whole grid pipeline is testable
 * without a DOM. `App.tsx` owns the state; this module owns the rules.
 */

import { clashingRegions, type ConflictMatrix } from "./conflicts";
import type { Conflict, Cosmetic } from "./api";

/** How exclusive an item is, within the selected class's items. */
export type Scope = "any" | "one" | "multi" | "all";

export type SortKey = "index" | "name" | "price";

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
  /** Refined-metal budget cap; null means no cap. Unpriced or non-ref-comparable
   * items (a currency with no metal exchange rate) are dropped once set, since their
   * affordability can't be verified. */
  maxRef: number | null;
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
  maxRef: null,
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
  if (sort === "price") return (a.price?.ref_value ?? 0) - (b.price?.ref_value ?? 0);
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
    if (state.maxRef !== null) {
      const rv = item.price?.ref_value;
      if (rv == null || rv > state.maxRef) return false;
    }
    return true;
  });

  // Unpriced items have nothing to sort by, so they always sink to the tail rather
  // than jumping to the front when `desc` reverses the priced ones.
  let sorted: Cosmetic[];
  if (state.sort === "price") {
    const priced = kept.filter((item) => item.price?.ref_value != null);
    const unpriced = kept.filter((item) => item.price?.ref_value == null);
    priced.sort((a, b) => compare(a, b, state.sort));
    if (state.desc) priced.reverse();
    sorted = [...priced, ...unpriced];
  } else {
    sorted = [...kept].sort((a, b) => compare(a, b, state.sort));
    if (state.desc) sorted.reverse();
  }

  return sorted.map((item) => {
    const clashesWith = state.noClashes
      ? tray
          .filter(
            (worn) =>
              worn.defindex !== item.defindex &&
              clashingRegions(item.equip_regions, worn.equip_regions, matrix).length >
                0
          )
          .map((worn) => worn.name)
      : [];
    return { item, dimmed: clashesWith.length > 0, clashesWith };
  });
}

/** Every defindex named on either side of a conflict list, for O(1) chip lookups. */
export function clashingIds(conflicts: Conflict[]): Set<number> {
  const s = new Set<number>();
  for (const c of conflicts) {
    s.add(c.a);
    s.add(c.b);
  }
  return s;
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
    state.maxRef !== null,
  ].filter(Boolean).length;
}
