import { afterEach, describe, expect, it, vi } from "vitest";
import type { Cosmetic } from "./api";
import { MAX_EQUIPPED } from "./conflicts";
import { DEFAULT_FILTERS, applyFilters } from "./filters";
import {
  MAX_LOADOUT_NAME_CHARS,
  cleanName,
  contentKey,
  importLoadouts,
  importSummary,
  mergeFresh,
  parseCosmetic,
  parseLoadoutList,
  parseLoadoutsFile,
  parseSavedLoadout,
  readStored,
  skippedText,
  type SavedLoadout,
} from "./savedLoadouts";

const STORAGE_KEY = "tf2-saved-loadouts/v1";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

function cosmetic(defindex: number, over: Partial<Cosmetic> = {}): Cosmetic {
  return {
    defindex,
    name: `Item ${defindex}`,
    equip_regions: ["hat"],
    used_by_classes: ["Spy"],
    item_slot: "misc",
    image_url: null,
    price: null,
    paintable: false,
    holiday_restriction: null,
    styles: [],
    ...over,
  };
}

function loadout(id: string, items: Cosmetic[] = [cosmetic(1)], name = "Build"): SavedLoadout {
  return { id, name, cls: "Spy", items, createdAt: 1, updatedAt: 2 };
}

afterEach(() => vi.unstubAllGlobals());

describe("parseCosmetic", () => {
  it("accepts a catalog-shaped cosmetic unchanged", () => {
    const c = cosmetic(7, {
      price: { currency: "metal", value: 5, value_high: 6, ref_value: 5 },
      paintable: true,
      styles: ["Red"],
      holiday_restriction: "halloween",
      image_url: "https://x/y.png",
    });
    expect(parseCosmetic(c)).toEqual(c);
  });

  it("upgrades a stored http image_url to https", () => {
    const c = cosmetic(7, { image_url: "http://media.steampowered.com/a.png" });
    expect(parseCosmetic(c)?.image_url).toBe("https://media.steampowered.com/a.png");
  });

  it("leaves a non-http image_url alone", () => {
    expect(parseCosmetic(cosmetic(7, { image_url: "/classes/spy.png" }))?.image_url).toBe(
      "/classes/spy.png",
    );
  });

  it("rejects a missing or non-array equip_regions", () => {
    expect(parseCosmetic({ defindex: 1, name: "Bad" })).toBeNull();
    expect(parseCosmetic({ defindex: 1, name: "Bad", equip_regions: "hat" })).toBeNull();
  });

  it("rejects non-string regions, a non-integer or non-positive defindex, a missing name, and non-objects", () => {
    expect(parseCosmetic({ ...cosmetic(1), equip_regions: ["hat", 3] })).toBeNull();
    expect(parseCosmetic({ ...cosmetic(1), defindex: 1.5 })).toBeNull();
    expect(parseCosmetic({ ...cosmetic(1), defindex: 0 })).toBeNull();
    expect(parseCosmetic({ ...cosmetic(1), defindex: "1" })).toBeNull();
    expect(parseCosmetic({ ...cosmetic(1), name: undefined })).toBeNull();
    expect(parseCosmetic(null)).toBeNull();
    expect(parseCosmetic("x")).toBeNull();
    expect(parseCosmetic([])).toBeNull();
  });

  it("defaults fields older saves lack (paintable, styles, price, holiday_restriction)", () => {
    const old = { defindex: 5, name: "Old", equip_regions: ["hat"] };
    expect(parseCosmetic(old)).toEqual({
      defindex: 5,
      name: "Old",
      equip_regions: ["hat"],
      used_by_classes: [],
      item_slot: null,
      image_url: null,
      price: null,
      paintable: false,
      holiday_restriction: null,
      styles: [],
    });
  });

  it("keeps a price saved before value_high and ref_value existed", () => {
    const old = { currency: "metal", value: 5 };
    expect(parseCosmetic({ ...cosmetic(1), price: old })?.price).toEqual({
      currency: "metal",
      value: 5,
      value_high: null,
      ref_value: null,
    });
  });

  it("drops a malformed price to null", () => {
    expect(parseCosmetic({ ...cosmetic(1), price: { currency: "metal", value: "5" } })?.price).toBeNull();
    expect(parseCosmetic({ ...cosmetic(1), price: "cheap" })?.price).toBeNull();
  });
});

describe("parseSavedLoadout", () => {
  it("rejects a missing id/name, non-array items, and an unknown class", () => {
    const ok = loadout("a");
    expect(parseSavedLoadout(ok)).not.toBeNull();
    expect(parseSavedLoadout({ ...ok, id: undefined })).toBeNull();
    expect(parseSavedLoadout({ ...ok, id: "" })).toBeNull();
    expect(parseSavedLoadout({ ...ok, name: 5 })).toBeNull();
    expect(parseSavedLoadout({ ...ok, items: "nope" })).toBeNull();
    expect(parseSavedLoadout({ ...ok, cls: "Wizard" })).toBeNull();
    expect(parseSavedLoadout(null)).toBeNull();
  });

  it("drops invalid items but keeps the loadout", () => {
    const l = parseSavedLoadout({ ...loadout("a"), items: [cosmetic(1), { defindex: 2, name: "x" }] });
    expect(l?.items.map((c) => c.defindex)).toEqual([1]);
  });

  it("dedupes items by defindex and caps at MAX_EQUIPPED", () => {
    const many = Array.from({ length: MAX_EQUIPPED + 5 }, (_, i) => cosmetic(i + 1));
    const l = parseSavedLoadout({ ...loadout("a"), items: [cosmetic(1), cosmetic(1), ...many] });
    expect(l?.items).toHaveLength(MAX_EQUIPPED);
    expect(new Set(l?.items.map((c) => c.defindex)).size).toBe(MAX_EQUIPPED);
  });

  it("truncates long names and falls back on blank ones", () => {
    expect(parseSavedLoadout({ ...loadout("a"), name: "x".repeat(300) })?.name).toHaveLength(
      MAX_LOADOUT_NAME_CHARS
    );
    expect(parseSavedLoadout({ ...loadout("a"), name: "   " })?.name).toBe("Untitled loadout");
  });

  it("fills non-numeric timestamps with now", () => {
    const l = parseSavedLoadout({ ...loadout("a"), createdAt: "x", updatedAt: null }, 1234);
    expect(l?.createdAt).toBe(1234);
    expect(l?.updatedAt).toBe(1234);
  });
});

describe("cleanName", () => {
  it("trims, truncates to MAX_LOADOUT_NAME_CHARS and falls back on blank", () => {
    expect(cleanName("  hi  ")).toBe("hi");
    expect(cleanName("y".repeat(150))).toHaveLength(MAX_LOADOUT_NAME_CHARS);
    expect(cleanName("   ")).toBe("Untitled loadout");
    expect(cleanName("", "Keep me")).toBe("Keep me");
  });

  it("never splits a surrogate pair at the boundary", () => {
    const out = cleanName("a".repeat(MAX_LOADOUT_NAME_CHARS - 1) + "😀" + "b");
    expect(Array.from(out)).toHaveLength(MAX_LOADOUT_NAME_CHARS);
    expect(out.endsWith("😀")).toBe(true);
    expect(/[\uD800-\uDBFF]$/.test(out)).toBe(false);
  });

  it("truncates a long fallback", () => {
    expect(Array.from(cleanName("", "z".repeat(200)))).toHaveLength(MAX_LOADOUT_NAME_CHARS);
  });
});

describe("parseLoadoutsFile", () => {
  it("throws on invalid JSON, a wrong version, or a missing loadouts array", () => {
    expect(() => parseLoadoutsFile("not json")).toThrow("Not a TF2 loadouts file.");
    expect(() => parseLoadoutsFile(JSON.stringify({ version: 2, loadouts: [] }))).toThrow();
    expect(() => parseLoadoutsFile(JSON.stringify({ version: 1 }))).toThrow();
    expect(() => parseLoadoutsFile("null")).toThrow();
  });

  it("counts rejected entries as invalid and dropped items as droppedItems", () => {
    const file = JSON.stringify({
      version: 1,
      loadouts: [
        { ...loadout("a"), items: [cosmetic(1), { defindex: 2 }, cosmetic(1)] },
        { nope: true },
        "junk",
      ],
    });
    const p = parseLoadoutsFile(file);
    expect(p.loadouts).toHaveLength(1);
    expect(p.invalid).toBe(2);
    expect(p.droppedItems).toBe(2);
  });

  it("a crafted item without equip_regions can no longer reach the clash filter", () => {
    const file = JSON.stringify({
      version: 1,
      loadouts: [{ id: "x", name: "evil", cls: "Spy", items: [{ defindex: 1, name: "Bad" }] }],
    });
    const result = parseLoadoutsFile(file);
    expect(result.loadouts[0].items).toEqual([]);
    expect(() =>
      applyFilters(
        [cosmetic(9)],
        { ...DEFAULT_FILTERS, noClashes: true },
        result.loadouts[0].items,
        {}
      )
    ).not.toThrow();
  });
});

describe("parseLoadoutList", () => {
  it("re-ids a repeated id so every record's id is unique", () => {
    const a = loadout("same", [cosmetic(1)], "A");
    const b = loadout("same", [cosmetic(2)], "B");
    const p = parseLoadoutList([a, b]);
    expect(p.loadouts).toHaveLength(2);
    expect(p.loadouts[0].id).toBe("same");
    expect(p.loadouts[1].id).not.toBe("same");
    expect(p.invalid).toBe(0);
  });

  it("derives the same ids on every read, so a held id still matches after a re-read", () => {
    const raw = [loadout("same", [cosmetic(1)], "A"), loadout("same", [cosmetic(2)], "B")];
    const first = parseLoadoutList(raw).loadouts.map((l) => l.id);
    const second = parseLoadoutList(raw).loadouts.map((l) => l.id);
    expect(second).toEqual(first);
  });
});

describe("contentKey", () => {
  it("ignores item order and id/timestamps", () => {
    const a = loadout("a", [cosmetic(1), cosmetic(2)]);
    const b = { ...loadout("b", [cosmetic(2), cosmetic(1)]), createdAt: 99, updatedAt: 100 };
    expect(contentKey(a)).toBe(contentKey(b));
  });

  it("matches a raw untrimmed or over-long name to its cleaned form", () => {
    const raw = loadout("a", [cosmetic(1)], "  " + "n".repeat(150) + "  ");
    const cleaned = loadout("b", [cosmetic(1)], "n".repeat(MAX_LOADOUT_NAME_CHARS));
    expect(contentKey(raw)).toBe(contentKey(cleaned));
  });
});

describe("mergeFresh", () => {
  it("replaces stored items with fresh ones in stored order", () => {
    const stored = [cosmetic(1), cosmetic(2)];
    const fresh = [cosmetic(2, { name: "New 2" }), cosmetic(1, { name: "New 1" })];
    const { items, dropped } = mergeFresh(stored, fresh);
    expect(items.map((c) => c.name)).toEqual(["New 1", "New 2"]);
    expect(dropped).toBe(0);
  });

  it("drops and counts items the catalog no longer has", () => {
    const { items, dropped } = mergeFresh([cosmetic(1), cosmetic(2)], [cosmetic(2)]);
    expect(items.map((c) => c.defindex)).toEqual([2]);
    expect(dropped).toBe(1);
  });
});

describe("skippedText", () => {
  it("returns null when nothing was skipped", () => {
    expect(skippedText({ invalid: 0, droppedItems: 0 }, "in that file")).toBeNull();
  });

  it("pluralises and omits the zero half", () => {
    expect(skippedText({ invalid: 2, droppedItems: 1 }, "in that file")).toBe(
      "Skipped 2 unreadable loadouts and 1 unreadable item in that file."
    );
    expect(skippedText({ invalid: 1, droppedItems: 0 }, "saved in this browser")).toBe(
      "Skipped 1 unreadable loadout saved in this browser."
    );
    expect(skippedText({ invalid: 0, droppedItems: 3 }, "in that file")).toBe(
      "Skipped 3 unreadable items in that file."
    );
  });
});

describe("importSummary", () => {
  it("pluralises and appends duplicates and skipped text only when present", () => {
    expect(importSummary(1, 0, null)).toBe("Imported 1 loadout.");
    expect(importSummary(3, 2, "Skipped 1 unreadable loadout in that file.")).toBe(
      "Imported 3 loadouts; 2 already saved. Skipped 1 unreadable loadout in that file."
    );
  });

  it("reports failures with the retry hint and keeps duplicates and skipped text", () => {
    const text = importSummary(2, 1, "Skipped 1 unreadable item in that file.", 3);
    expect(text).toContain("Imported 2 of 5");
    expect(text).toContain("3 couldn't be saved");
    expect(text).toContain("import the same file again to retry");
    expect(text).toContain("1 were already saved");
    expect(text).toContain("Skipped 1 unreadable item in that file.");
  });
});

describe("storage", () => {
  it("readStored drops corrupted entries and counts them", () => {
    const storage = fakeStorage({
      [STORAGE_KEY]: JSON.stringify({
        version: 1,
        loadouts: [loadout("good"), { id: "x", name: "evil", cls: "Spy", items: [{ defindex: 1 }] }, 5],
      }),
    });
    vi.stubGlobal("localStorage", storage);
    const r = readStored();
    expect(r.loadouts.map((l) => l.id)).toEqual(["good", "x"]);
    expect(r.loadouts[1].items).toEqual([]);
    expect(r.invalid).toBe(1);
    expect(r.droppedItems).toBe(1);
  });

  it("importLoadouts skips content duplicates and re-ids colliding ids", () => {
    const storage = fakeStorage({
      [STORAGE_KEY]: JSON.stringify({ version: 1, loadouts: [loadout("keep", [cosmetic(1)], "Mine")] }),
    });
    vi.stubGlobal("localStorage", storage);
    const result = importLoadouts([
      loadout("keep", [cosmetic(2)], "Collides"), // id collision, different content
      loadout("other", [cosmetic(1)], "Mine"), // same content as the stored one
      loadout("dup1", [cosmetic(3)], "Twin"),
      loadout("dup1", [cosmetic(4)], "Twin 2"), // repeated id inside the file
    ]);
    expect(result.added).toBe(3);
    expect(result.duplicates).toBe(1);
    const ids = result.loadouts.map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain("keep");
    expect(JSON.parse(storage.data.get(STORAGE_KEY)!).loadouts).toHaveLength(4);
  });
});
