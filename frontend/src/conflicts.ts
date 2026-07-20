/**
 * Equip-region conflict rule, ported from `src/tf2_loadout/conflicts.py`.
 *
 * The browser needs this locally so filter toggles are instant instead of a round-trip
 * per click. Python stays the source of truth: `conflicts.test.ts` mirrors
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
