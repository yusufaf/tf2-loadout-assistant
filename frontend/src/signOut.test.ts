import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TRANSCRIPT_KEY, serializeTranscript } from "./chatSession";
import { STORAGE_KEY, serializeKey } from "./llmKey";
import { signOutAndForget } from "./signOut";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

const KEY = serializeKey({ provider: "anthropic", apiKey: "sk-abc" });
const TRANSCRIPT = serializeTranscript({
  turns: [
    { role: "user", text: "cop look" },
    { role: "bot", text: "Try this" },
  ],
  history: [{ kind: "request" }, { kind: "response" }],
  owner: "76561190000000001",
});

let local: ReturnType<typeof fakeStorage>;
let session: ReturnType<typeof fakeStorage>;

beforeEach(() => {
  local = fakeStorage({ [TRANSCRIPT_KEY]: TRANSCRIPT, [STORAGE_KEY]: KEY });
  session = fakeStorage({ [STORAGE_KEY]: KEY });
  vi.stubGlobal("localStorage", local);
  vi.stubGlobal("sessionStorage", session);
});

afterEach(() => vi.unstubAllGlobals());

function expectForgotten() {
  expect(local.data.has(TRANSCRIPT_KEY)).toBe(false);
  expect(local.data.has(STORAGE_KEY)).toBe(false);
  expect(session.data.has(STORAGE_KEY)).toBe(false);
}

describe("signOutAndForget", () => {
  it("forgets the key and transcript even when the logout request fails", async () => {
    const request = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await signOutAndForget(request)).toBe(false);
    expectForgotten();
  });

  it("clears both before the request is sent", async () => {
    const request = vi.fn(async () => expectForgotten());
    expect(await signOutAndForget(request)).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("resolves true once the API has cleared the session", async () => {
    expect(await signOutAndForget(vi.fn().mockResolvedValue(undefined))).toBe(true);
    expectForgotten();
  });
});
