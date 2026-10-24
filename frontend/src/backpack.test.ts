import { describe, expect, it } from "vitest";
import type { Inventory, InventoryStatus } from "./api";
import {
  backpackBusy,
  backpackFailed,
  backpackRefreshFailed,
  ownedDefindexes,
  ownedDisabledReason,
  ownedEnabled,
  reloadRequest,
  settleBackpack,
  type BackpackState,
} from "./backpack";

function inv(status: InventoryStatus, defindexes: number[] = []): Inventory {
  return { status, defindexes, fetched_at: 1 };
}

function loaded(
  inventory: Inventory,
  over: Partial<{ refreshing: boolean; refreshFailed: boolean }> = {}
): BackpackState {
  return { kind: "loaded", inventory, refreshing: false, refreshFailed: false, ...over };
}

const SIGNED_OUT: BackpackState = { kind: "signed_out" };
const LOADING: BackpackState = { kind: "loading" };
const FAILED: BackpackState = { kind: "failed", retrying: false };
const RETRYING: BackpackState = { kind: "failed", retrying: true };

describe("settleBackpack", () => {
  it("turns a failed first read into failed, not loading", () => {
    expect(settleBackpack(LOADING, null)).toEqual({ kind: "failed", retrying: false });
  });

  it("keeps a failed retry failed, no longer retrying", () => {
    expect(settleBackpack(RETRYING, null)).toEqual({ kind: "failed", retrying: false });
  });

  it("keeps the last read when a refresh fails", () => {
    const ok = inv("ok", [5, 6]);
    expect(settleBackpack(loaded(ok, { refreshing: true }), null)).toEqual(
      loaded(ok, { refreshFailed: true })
    );
  });

  it("keeps a good read when Steam errors on refresh", () => {
    const ok = inv("ok", [5, 6]);
    expect(settleBackpack(loaded(ok, { refreshing: true }), inv("error"))).toEqual(
      loaded(ok, { refreshFailed: true })
    );
  });

  it("keeps a private read when Steam errors on refresh", () => {
    const priv = inv("private");
    expect(settleBackpack(loaded(priv, { refreshing: true }), inv("error"))).toEqual(
      loaded(priv, { refreshFailed: true })
    );
  });

  it("replaces an error read with a new error read", () => {
    const next = { ...inv("error"), fetched_at: 2 };
    expect(settleBackpack(loaded(inv("error"), { refreshing: true }), next)).toEqual(
      loaded(next)
    );
  });

  it("keeps an error read when its retry fails outright", () => {
    const err = inv("error");
    expect(settleBackpack(loaded(err, { refreshing: true }), null)).toEqual(
      loaded(err, { refreshFailed: true })
    );
  });

  it.each([
    ["loading", LOADING],
    ["a retry", RETRYING],
    ["a refresh", loaded(inv("ok", [1]), { refreshing: true, refreshFailed: true })],
  ])("takes a fresh read after %s", (_, prev) => {
    const fresh = inv("ok", [5]);
    expect(settleBackpack(prev, fresh)).toEqual(loaded(fresh));
  });

  it("takes an error read as the first read", () => {
    expect(settleBackpack(LOADING, inv("error"))).toEqual(loaded(inv("error")));
  });
});

describe("reloadRequest", () => {
  it.each([
    ["signed out", SIGNED_OUT],
    ["loading", LOADING],
    ["retrying", RETRYING],
    ["refreshing", loaded(inv("ok"), { refreshing: true })],
  ])("does nothing while %s", (_, b) => {
    expect(reloadRequest(b)).toBeNull();
  });

  it("retries a failed read without spending refresh quota", () => {
    expect(reloadRequest(FAILED)).toEqual({ next: RETRYING, refresh: false });
  });

  it("re-asks Steam after an error without spending refresh quota", () => {
    const err = inv("error");
    expect(reloadRequest(loaded(err))).toEqual({
      next: loaded(err, { refreshing: true }),
      refresh: false,
    });
  });

  it("refreshes a good read past the server cache and clears the old failure", () => {
    const ok = inv("ok", [5]);
    expect(reloadRequest(loaded(ok, { refreshFailed: true }))).toEqual({
      next: loaded(ok, { refreshing: true }),
      refresh: true,
    });
  });

  it("refreshes a private read past the server cache", () => {
    const priv = inv("private");
    expect(reloadRequest(loaded(priv))).toEqual({
      next: loaded(priv, { refreshing: true }),
      refresh: true,
    });
  });
});

describe("ownedEnabled / ownedDefindexes", () => {
  it.each([
    ["idle", loaded(inv("ok", [5, 6]))],
    ["refreshing", loaded(inv("ok", [5, 6]), { refreshing: true })],
    ["after a failed refresh", loaded(inv("ok", [5, 6]), { refreshFailed: true })],
  ])("uses a good read while %s", (_, b) => {
    expect(ownedEnabled(b)).toBe(true);
    expect(ownedDefindexes(b)).toEqual([5, 6]);
  });

  it.each([
    ["failed", FAILED],
    ["loading", LOADING],
    ["private", loaded(inv("private", [5]))],
  ])("owns nothing when %s", (_, b) => {
    expect(ownedEnabled(b)).toBe(false);
    expect(ownedDefindexes(b)).toEqual([]);
  });
});

describe("backpackFailed", () => {
  it.each([
    ["failed", FAILED],
    ["retrying", RETRYING],
    ["a Steam error", loaded(inv("error"))],
    ["a Steam error being retried", loaded(inv("error"), { refreshing: true })],
  ])("is true when %s", (_, b) => {
    expect(backpackFailed(b)).toBe(true);
  });

  it.each([
    ["loading", LOADING],
    ["signed out", SIGNED_OUT],
    ["ok", loaded(inv("ok"))],
    ["private", loaded(inv("private"))],
  ])("is false when %s", (_, b) => {
    expect(backpackFailed(b)).toBe(false);
  });
});

describe("backpackBusy", () => {
  it.each([
    ["loading", LOADING],
    ["retrying", RETRYING],
    ["refreshing", loaded(inv("ok"), { refreshing: true })],
  ])("is true when %s", (_, b) => {
    expect(backpackBusy(b)).toBe(true);
  });

  it.each([
    ["signed out", SIGNED_OUT],
    ["failed", FAILED],
    ["loaded", loaded(inv("ok"))],
  ])("is false when %s", (_, b) => {
    expect(backpackBusy(b)).toBe(false);
  });
});

describe("backpackRefreshFailed", () => {
  it.each([
    ["ok", loaded(inv("ok"), { refreshFailed: true })],
    ["private", loaded(inv("private"), { refreshFailed: true })],
  ])("is true for a %s read whose refresh failed", (_, b) => {
    expect(backpackRefreshFailed(b)).toBe(true);
  });

  it.each([
    // A failed state shows only its own note.
    ["a Steam error", loaded(inv("error"), { refreshFailed: true })],
    ["a good read", loaded(inv("ok"))],
    ["failed", FAILED],
    ["loading", LOADING],
  ])("is false for %s", (_, b) => {
    expect(backpackRefreshFailed(b)).toBe(false);
  });
});

describe("ownedDisabledReason", () => {
  it("explains each state", () => {
    expect(ownedDisabledReason(SIGNED_OUT)).toBe(
      "Sign in through Steam to filter by what you own"
    );
    expect(ownedDisabledReason(LOADING)).toBe("Loading your backpack…");
    expect(ownedDisabledReason(FAILED)).toBe("Couldn't load your backpack");
    expect(ownedDisabledReason(RETRYING)).toBe("Couldn't load your backpack");
    expect(ownedDisabledReason(loaded(inv("private")))).toBe(
      "Your backpack is set to private on Steam"
    );
    expect(ownedDisabledReason(loaded(inv("not_found")))).toBe(
      "Couldn't find a TF2 backpack for this SteamID"
    );
    expect(ownedDisabledReason(loaded(inv("error")))).toBe(
      "Couldn't reach Steam's inventory API"
    );
    expect(ownedDisabledReason(loaded(inv("ok")))).toBe("");
  });

  it("never reads a failed read as still loading", () => {
    expect(ownedDisabledReason(FAILED)).not.toBe(ownedDisabledReason(LOADING));
  });
});
