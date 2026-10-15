import { describe, expect, it } from "vitest";
import { decodeLoadout, encodeLoadout } from "./shareLink";
import { MAX_EQUIPPED } from "./conflicts";
import type { Cosmetic } from "./api";

// encodeLoadout only reads `defindex`.
const items = (ids: number[]) => ids.map((defindex) => ({ defindex }) as Cosmetic);

describe("decodeLoadout", () => {
  it("round-trips a class and its items", () => {
    expect(decodeLoadout(encodeLoadout("Spy", items([5, 7])))).toEqual({
      cls: "Spy",
      defindexes: [5, 7],
    });
  });

  it("de-duplicates and caps the bench so a crafted link can't flood the API", () => {
    const ids = [1, 1, ...Array.from({ length: 30 }, (_, i) => i + 2)];
    const decoded = decodeLoadout(encodeLoadout("Spy", items(ids)));
    expect(decoded?.defindexes).toHaveLength(MAX_EQUIPPED);
    expect(decoded?.defindexes.slice(0, 2)).toEqual([1, 2]);
  });

  it("rejects an unknown class, which would otherwise ride into every chat prompt", () => {
    expect(decodeLoadout(encodeLoadout("x".repeat(500), items([1])))).toBeNull();
  });

  it("rejects garbage", () => {
    expect(decodeLoadout("not-base64!!")).toBeNull();
  });
});
