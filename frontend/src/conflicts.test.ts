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
    expect(clashingRegions(["hat"], ["whole_head"], { whole_head: ["hat"] })).toEqual([
      "hat",
      "whole_head",
    ]);
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
