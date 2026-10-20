import { describe, expect, it } from "vitest";
import type { Cosmetic, LoadoutRecord } from "./api";
import { hydrateRecords, importToAccount } from "./remoteLoadouts";
import type { SavedLoadout } from "./savedLoadouts";

function cosmetic(defindex: number): Cosmetic {
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
  };
}

function record(id: string, defindexes: number[]): LoadoutRecord {
  return { id, name: `Build ${id}`, cls: "Spy", defindexes, created_at: 1, updated_at: 2 };
}

function loadout(id: string, name: string, defindexes: number[]): SavedLoadout {
  return {
    id,
    name,
    cls: "Spy",
    items: defindexes.map(cosmetic),
    createdAt: 1,
    updatedAt: 2,
  };
}

describe("hydrateRecords", () => {
  it("keeps record and defindex order", () => {
    const byDefindex = new Map([1, 2, 3].map((d) => [d, cosmetic(d)]));
    const out = hydrateRecords([record("b", [3, 1]), record("a", [2])], byDefindex);
    expect(out.map((l) => l.id)).toEqual(["b", "a"]);
    expect(out[0].items.map((c) => c.defindex)).toEqual([3, 1]);
    expect(out[0].createdAt).toBe(1);
    expect(out[0].updatedAt).toBe(2);
  });

  it("counts defindexes the catalog no longer has as unavailable", () => {
    const byDefindex = new Map([[1, cosmetic(1)]]);
    const [partial, complete] = hydrateRecords(
      [record("a", [1, 98, 99]), record("b", [1])],
      byDefindex
    );
    expect(partial.items.map((c) => c.defindex)).toEqual([1]);
    expect(partial.unavailable).toBe(2);
    expect("unavailable" in complete).toBe(false);
  });
});

describe("importToAccount", () => {
  it("creates sequentially in input order", async () => {
    const log: string[] = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const createFn = async (l: SavedLoadout) => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      log.push(l.name);
      await Promise.resolve();
      inFlight--;
      return { ...l, id: `server-${l.name}` };
    };
    const result = await importToAccount(
      [loadout("1", "A", [1]), loadout("2", "B", [2]), loadout("3", "C", [3])],
      [],
      createFn
    );
    expect(log).toEqual(["A", "B", "C"]);
    expect(maxInFlight).toBe(1);
    expect(result.created.map((l) => l.id)).toEqual(["server-A", "server-B", "server-C"]);
    expect(result.failed).toBe(0);
  });

  it("skips loadouts already on the account and repeats within the file", async () => {
    const createFn = async (l: SavedLoadout) => l;
    const result = await importToAccount(
      [
        loadout("1", "Mine", [1, 2]), // already on the account
        loadout("2", "New", [3]),
        loadout("3", "New", [3]), // repeat inside the file
      ],
      [loadout("x", "Mine", [2, 1])],
      createFn
    );
    expect(result.created.map((l) => l.name)).toEqual(["New"]);
    expect(result.duplicates).toBe(2);
  });

  it("keeps going after a failed create and reports the failure count", async () => {
    const createFn = async (l: SavedLoadout) => {
      if (l.name === "B") throw new Error("boom");
      return l;
    };
    const result = await importToAccount(
      [loadout("1", "A", [1]), loadout("2", "B", [2]), loadout("3", "C", [3])],
      [],
      createFn
    );
    expect(result.created.map((l) => l.name)).toEqual(["A", "C"]);
    expect(result.failed).toBe(1);
  });
});
