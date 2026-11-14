import { describe, expect, it } from "vitest";
import { LoadoutWriteUnconfirmedError, type Cosmetic, type LoadoutRecord } from "./api";
import { copySummary, describeWriteFailure, hydrateRecords, importToAccount } from "./remoteLoadouts";
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
    expect(result.unconfirmed).toBe(0);
    expect(result.notTried).toBe(0);
  });

  it("stops at the first create it couldn't confirm and counts the rest as not tried", async () => {
    let calls = 0;
    const createFn = async (l: SavedLoadout) => {
      calls++;
      if (l.name === "B") throw new LoadoutWriteUnconfirmedError("x");
      return l;
    };
    const result = await importToAccount(
      [
        loadout("1", "A", [1]),
        loadout("2", "B", [2]),
        loadout("3", "C", [3]),
        loadout("4", "D", [4]),
        loadout("5", "D", [4]), // repeat inside the file, after the stop
      ],
      [],
      createFn
    );
    expect(result.created.map((l) => l.name)).toEqual(["A"]);
    expect(result.failed).toBe(0);
    expect(result.unconfirmed).toBe(1);
    expect(result.notTried).toBe(2);
    expect(result.duplicates).toBe(1);
    expect(calls).toBe(2);
  });

  it("keeps going after a plain failure and stops only at an unconfirmed one", async () => {
    let calls = 0;
    const createFn = async (l: SavedLoadout) => {
      calls++;
      if (l.name === "B") throw new Error("boom");
      if (l.name === "C") throw new LoadoutWriteUnconfirmedError("x");
      return l;
    };
    const result = await importToAccount(
      [loadout("1", "A", [1]), loadout("2", "B", [2]), loadout("3", "C", [3]), loadout("4", "D", [4])],
      [],
      createFn
    );
    expect(result.created.map((l) => l.name)).toEqual(["A"]);
    expect(result.failed).toBe(1);
    expect(result.unconfirmed).toBe(1);
    expect(result.notTried).toBe(1);
    expect(calls).toBe(3);
  });
});

describe("describeWriteFailure", () => {
  const actions = ["save", "rename", "delete", "import"] as const;

  it.each([
    ["save", "Couldn't save the loadout to your Steam account. Try again."],
    ["rename", "Couldn't rename the loadout. Try again."],
    ["delete", "Couldn't delete the loadout. Try again."],
    ["import", "Couldn't import to your account. Try again."],
  ] as const)("keeps the %s failure wording, with no re-read, when the server answered", (action, text) => {
    expect(describeWriteFailure(new Error("loadouts 422"), action)).toEqual({ text, reread: false });
  });

  it.each(actions)("asks for a re-read on an unconfirmed %s, never claiming it finished", (action) => {
    const failure = describeWriteFailure(new LoadoutWriteUnconfirmedError("x"), action);
    expect(failure.reread).toBe(true);
    expect(failure.text).toMatch(/couldn't confirm/i);
    expect(failure.text).toMatch(/re-reading/);
    expect(failure.text).not.toMatch(/try again/i);
    expect(failure.text).not.toMatch(/was reloaded|was re-read/);
  });

  it.each(["save", "import"] as const)("tells the player to wait before another %s", (action) => {
    const { text } = describeWriteFailure(new LoadoutWriteUnconfirmedError("x"), action);
    expect(text).toMatch(/wait a minute and reload/);
    expect(text).toMatch(/reload the page before/);
  });
});

describe("copySummary", () => {
  const one = loadout("a", "A", [1]);

  it("says nothing when every build was copied or already there", () => {
    expect(
      copySummary({ created: [one], duplicates: 2, failed: 0, unconfirmed: 0, notTried: 0 }, 3)
    ).toBeNull();
  });

  it("keeps today's text for plain failures, without a re-read", () => {
    expect(
      copySummary({ created: [one], duplicates: 0, failed: 2, unconfirmed: 0, notTried: 0 }, 3)
    ).toEqual({
      reread: false,
      text: "Copied 1 of 3 local loadouts to your account; the rest are still saved in this browser. To copy them, sign out, Export, sign back in and Import — builds already copied are skipped.",
    });
  });

  it("asks for a re-read and a wait when a copy couldn't be confirmed", () => {
    const summary = copySummary(
      { created: [one], duplicates: 0, failed: 1, unconfirmed: 1, notTried: 1 },
      4
    );
    expect(summary?.reread).toBe(true);
    expect(summary?.text).toContain("Copied 1 of 4");
    expect(summary?.text).toContain("1 couldn't be confirmed");
    expect(summary?.text).toContain("being re-read");
    expect(summary?.text).toContain("wait a minute");
    expect(summary?.text).toContain("still saved in this browser");
    expect(summary?.text).not.toMatch(/try again/i);
  });
});
