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
    "Scout",
    "Soldier",
    "Pyro",
    "Demoman",
    "Heavy",
    "Engineer",
    "Medic",
    "Sniper",
    "Spy",
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
