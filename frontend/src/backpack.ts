/**
 * What the bench knows about the signed-in player's backpack, and what to do next.
 *
 * Pure functions with no React dependency, same as `filters.ts`: `App.tsx` owns the
 * state, this module owns the rules.
 *
 * `failed` is its own state rather than a `loading` that never ends, because only
 * `loading` resolves by itself; a failed read needs the player to press Retry. A
 * failed refresh keeps the last read instead of replacing it: a 429 from the
 * 3-a-minute refresh limit, a network blip, or Steam itself failing (which the API
 * reports as a 200 with `status: "error"` and no defindexes) says nothing about what
 * the player owns, so it must not wipe a backpack we already read. A retry stays in
 * its failed state while in flight, so the note and its Retry stay put, disabled,
 * instead of flickering away and back.
 */

import type { Inventory } from "./api";

export type BackpackState =
  | { kind: "signed_out" }
  | { kind: "loading" }
  | { kind: "failed"; retrying: boolean }
  | { kind: "loaded"; inventory: Inventory; refreshing: boolean; refreshFailed: boolean };

/** Folds a finished read (`null` when it failed) into the state it was made from. */
export function settleBackpack(prev: BackpackState, result: Inventory | null): BackpackState {
  if (
    prev.kind === "loaded" &&
    (result === null ||
      (result.status === "error" && prev.inventory.status !== "error"))
  ) {
    return { ...prev, refreshing: false, refreshFailed: true };
  }
  if (result) {
    return { kind: "loaded", inventory: result, refreshing: false, refreshFailed: false };
  }
  return { kind: "failed", retrying: false };
}

/** The state to show while re-reading, and whether to bypass the server's cache; null
 * when there's nothing to re-read or a read is already in flight. */
export function reloadRequest(
  b: BackpackState
): { next: BackpackState; refresh: boolean } | null {
  switch (b.kind) {
    case "signed_out":
    case "loading":
      return null;
    case "failed":
      return b.retrying ? null : { next: { kind: "failed", retrying: true }, refresh: false };
    case "loaded":
      if (b.refreshing) return null;
      // The server never caches an error, so a plain read re-asks Steam without
      // spending the refresh quota.
      return {
        next: { ...b, refreshing: true, refreshFailed: false },
        refresh: b.inventory.status !== "error",
      };
  }
}

export function ownedEnabled(b: BackpackState): boolean {
  return b.kind === "loaded" && b.inventory.status === "ok";
}

export function ownedDefindexes(b: BackpackState): number[] {
  return b.kind === "loaded" && b.inventory.status === "ok" ? b.inventory.defindexes : [];
}

/** The read itself failed, ours or Steam's: show "couldn't load" with a Retry. */
export function backpackFailed(b: BackpackState): boolean {
  return b.kind === "failed" || (b.kind === "loaded" && b.inventory.status === "error");
}

export function backpackBusy(b: BackpackState): boolean {
  return (
    b.kind === "loading" ||
    (b.kind === "failed" && b.retrying) ||
    (b.kind === "loaded" && b.refreshing)
  );
}

/** A refresh failed but an earlier read is still showing. */
export function backpackRefreshFailed(b: BackpackState): boolean {
  return b.kind === "loaded" && b.refreshFailed && !backpackFailed(b);
}

export function ownedDisabledReason(b: BackpackState): string {
  switch (b.kind) {
    case "signed_out":
      return "Sign in through Steam to filter by what you own";
    case "loading":
      return "Loading your backpack…";
    case "failed":
      return "Couldn't load your backpack";
    case "loaded":
      switch (b.inventory.status) {
        case "private":
          return "Your backpack is set to private on Steam";
        case "not_found":
          return "Couldn't find a TF2 backpack for this SteamID";
        case "error":
          return "Couldn't reach Steam's inventory API";
        case "ok":
          return "";
      }
  }
}
