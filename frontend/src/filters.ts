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
              clashingRegions(item.equip_regions, worn.equip_regions, matrix).length >
                0
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
