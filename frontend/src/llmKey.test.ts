import { afterEach, describe, expect, it, vi } from "vitest";
import {
  STORAGE_KEY,
  clearLlmKey,
  loadLlmKey,
  parseStoredKey,
  saveLlmKey,
  serializeKey,
} from "./llmKey";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("parseStoredKey", () => {
  it("round-trips through serializeKey", () => {
    const key = { provider: "anthropic", apiKey: "sk-abc" };
    expect(parseStoredKey(serializeKey(key))).toEqual(key);
  });

  it("returns null for nothing stored, garbage, or the wrong version", () => {
    expect(parseStoredKey(null)).toBeNull();
    expect(parseStoredKey("not json")).toBeNull();
    expect(
      parseStoredKey(JSON.stringify({ version: 2, provider: "anthropic", apiKey: "x" }))
    ).toBeNull();
  });

  it("rejects an envelope missing either field or with a blank key", () => {
    expect(parseStoredKey(JSON.stringify({ version: 1, provider: "anthropic" }))).toBeNull();
    expect(parseStoredKey(JSON.stringify({ version: 1, apiKey: "x" }))).toBeNull();
    expect(
      parseStoredKey(JSON.stringify({ version: 1, provider: "anthropic", apiKey: "  " }))
    ).toBeNull();
  });
});

describe("localStorage wrappers", () => {
  it("save then load returns the key; clear removes it", () => {
    const storage = fakeStorage();
    vi.stubGlobal("localStorage", storage);

    expect(loadLlmKey()).toBeNull();
    saveLlmKey({ provider: "openai", apiKey: "sk-1" });
    expect(storage.data.has(STORAGE_KEY)).toBe(true);
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk-1" });
    clearLlmKey();
    expect(loadLlmKey()).toBeNull();
  });

  it("trims the key before storing it", () => {
    vi.stubGlobal("localStorage", fakeStorage());
    saveLlmKey({ provider: "openai", apiKey: "  sk-1\n" });
    expect(loadLlmKey()?.apiKey).toBe("sk-1");
  });

  it("survives storage that throws (private mode, quota)", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    expect(loadLlmKey()).toBeNull();
    expect(() => saveLlmKey({ provider: "openai", apiKey: "sk" })).not.toThrow();
    expect(() => clearLlmKey()).not.toThrow();
  });

  it("returns null when localStorage is not defined at all", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(loadLlmKey()).toBeNull();
  });
});
