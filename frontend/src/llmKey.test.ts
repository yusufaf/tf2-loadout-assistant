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

function throwingStorage() {
  return {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
    removeItem: () => {
      throw new Error("blocked");
    },
  };
}

function stubStores(local: unknown = fakeStorage(), session: unknown = fakeStorage()) {
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
}

describe("storage wrappers", () => {
  it("save then load returns the key; clear removes it", () => {
    const local = fakeStorage();
    stubStores(local);

    expect(loadLlmKey()).toBeNull();
    saveLlmKey({ provider: "openai", apiKey: "sk-1" }, true);
    expect(local.data.has(STORAGE_KEY)).toBe(true);
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk-1", remember: true });
    clearLlmKey();
    expect(loadLlmKey()).toBeNull();
  });

  it("trims the key before storing it", () => {
    stubStores();
    saveLlmKey({ provider: "openai", apiKey: "  sk-1\n" }, true);
    expect(loadLlmKey()?.apiKey).toBe("sk-1");
  });

  it("remember=false keeps the key in sessionStorage only", () => {
    const local = fakeStorage();
    const session = fakeStorage();
    stubStores(local, session);

    saveLlmKey({ provider: "anthropic", apiKey: "sk-tab" }, false);
    expect(session.data.has(STORAGE_KEY)).toBe(true);
    expect(local.data.has(STORAGE_KEY)).toBe(false);
    expect(loadLlmKey()).toEqual({ provider: "anthropic", apiKey: "sk-tab", remember: false });
  });

  it("switching to this-tab-only removes the saved copy", () => {
    const local = fakeStorage();
    stubStores(local);

    saveLlmKey({ provider: "openai", apiKey: "sk-1" }, true);
    saveLlmKey({ provider: "openai", apiKey: "sk-2" }, false);
    expect(local.data.has(STORAGE_KEY)).toBe(false);
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk-2", remember: false });
  });

  it("switching to remembered removes the tab copy", () => {
    const session = fakeStorage();
    stubStores(fakeStorage(), session);

    saveLlmKey({ provider: "openai", apiKey: "sk-1" }, false);
    saveLlmKey({ provider: "openai", apiKey: "sk-2" }, true);
    expect(session.data.has(STORAGE_KEY)).toBe(false);
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk-2", remember: true });
  });

  it("clear removes the key from both stores", () => {
    const stored = serializeKey({ provider: "openai", apiKey: "sk" });
    const local = fakeStorage({ [STORAGE_KEY]: stored });
    const session = fakeStorage({ [STORAGE_KEY]: stored });
    stubStores(local, session);

    clearLlmKey();
    expect(local.data.has(STORAGE_KEY)).toBe(false);
    expect(session.data.has(STORAGE_KEY)).toBe(false);
  });

  it("a tab copy wins over a saved one", () => {
    stubStores(
      fakeStorage({ [STORAGE_KEY]: serializeKey({ provider: "openai", apiKey: "sk-saved" }) }),
      fakeStorage({ [STORAGE_KEY]: serializeKey({ provider: "anthropic", apiKey: "sk-tab" }) })
    );
    expect(loadLlmKey()).toEqual({ provider: "anthropic", apiKey: "sk-tab", remember: false });
  });

  it("choosing this tab only removes the saved copy even if the tab store fails", () => {
    const local = fakeStorage({
      [STORAGE_KEY]: serializeKey({ provider: "openai", apiKey: "sk-old" }),
    });
    stubStores(local, throwingStorage());

    expect(() => saveLlmKey({ provider: "openai", apiKey: "sk-new" }, false)).not.toThrow();
    expect(local.data.has(STORAGE_KEY)).toBe(false);
  });

  it("one store throwing doesn't stop the other", () => {
    const session = fakeStorage();
    stubStores(throwingStorage(), session);

    saveLlmKey({ provider: "openai", apiKey: "sk" }, false);
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk", remember: false });
    expect(() => clearLlmKey()).not.toThrow();
    expect(session.data.has(STORAGE_KEY)).toBe(false);
  });

  it("survives storage that throws (private mode, quota)", () => {
    stubStores(throwingStorage(), throwingStorage());
    expect(loadLlmKey()).toBeNull();
    expect(() => saveLlmKey({ provider: "openai", apiKey: "sk" }, true)).not.toThrow();
    expect(() => saveLlmKey({ provider: "openai", apiKey: "sk" }, false)).not.toThrow();
    expect(() => clearLlmKey()).not.toThrow();
  });

  it("returns null when neither store is defined at all", () => {
    stubStores(undefined, undefined);
    expect(loadLlmKey()).toBeNull();
    expect(() => saveLlmKey({ provider: "openai", apiKey: "sk" }, false)).not.toThrow();
    expect(() => clearLlmKey()).not.toThrow();
  });
});
