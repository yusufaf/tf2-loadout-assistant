import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TRANSCRIPT_KEY, serializeTranscript } from "./chatSession";
import { STORAGE_KEY, serializeKey } from "./llmKey";
import {
  SIGN_OUT_CHANNEL,
  listenForSignOut,
  signOutAndForget,
  type SignOutNews,
} from "./signOut";

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

// Channels and listeners each test opened; an open channel keeps the worker alive.
const open: Array<() => void> = [];

afterEach(() => {
  open.splice(0).forEach((close) => close());
  vi.unstubAllGlobals();
});

function expectForgotten() {
  expect(local.data.has(TRANSCRIPT_KEY)).toBe(false);
  expect(local.data.has(STORAGE_KEY)).toBe(false);
  expect(session.data.has(STORAGE_KEY)).toBe(false);
}

const MARKER = "settled";
const ok = () => vi.fn().mockResolvedValue(undefined);

/** A channel standing in for another open tab of this browser. */
function otherTab() {
  const channel = new BroadcastChannel(SIGN_OUT_CHANNEL);
  open.push(() => channel.close());
  const heard: unknown[] = [];
  channel.onmessage = (e: MessageEvent) => {
    if (e.data !== MARKER) heard.push(e.data);
  };
  return { heard, post: (data: unknown) => channel.postMessage(data) };
}

/** This tab's listener, recording the news it passes on. */
function listen(): SignOutNews[] {
  const news: SignOutNews[] = [];
  open.push(listenForSignOut((n) => news.push(n)));
  return news;
}

/** Waits until everything already posted on the channel has been delivered: a marker
 * posted after it arrives after it. */
async function settle(): Promise<void> {
  const watcher = new BroadcastChannel(SIGN_OUT_CHANNEL);
  const arrived = new Promise<void>((resolve) => {
    watcher.onmessage = (e: MessageEvent) => {
      if (e.data === MARKER) resolve();
    };
  });
  const marker = new BroadcastChannel(SIGN_OUT_CHANNEL);
  marker.postMessage(MARKER);
  marker.close();
  await arrived;
  watcher.close();
}

describe("signOutAndForget", () => {
  it("forgets the key and transcript even when the logout request fails", async () => {
    const request = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await signOutAndForget(request)).toBe(false);
    expectForgotten();
  });

  it("treats a timed-out logout request as a failure", async () => {
    const request = vi
      .fn()
      .mockRejectedValue(new DOMException("signal timed out", "TimeoutError"));
    expect(await signOutAndForget(request)).toBe(false);
    expectForgotten();
  });

  it("clears both before the request is sent", async () => {
    const request = vi.fn(async () => expectForgotten());
    expect(await signOutAndForget(request)).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("forgets a key or turn saved while the request was in flight", async () => {
    const request = vi.fn(async () => {
      local.setItem(TRANSCRIPT_KEY, TRANSCRIPT);
      local.setItem(STORAGE_KEY, KEY);
      session.setItem(STORAGE_KEY, KEY);
    });
    expect(await signOutAndForget(request)).toBe(true);
    expectForgotten();
  });

  it("resolves true once the API has cleared the session", async () => {
    expect(await signOutAndForget(vi.fn().mockResolvedValue(undefined))).toBe(true);
    expectForgotten();
  });
});

describe("other tabs", () => {
  it("tells them when it starts signing out and when it has signed out", async () => {
    const tab = otherTab();
    expect(await signOutAndForget(ok())).toBe(true);
    await settle();
    expect(tab.heard).toEqual([
      { kind: "forget", from: expect.any(String) },
      { kind: "signed_out", from: expect.any(String) },
    ]);
  });

  it("tells them to forget even when the logout request fails", async () => {
    const tab = otherTab();
    const request = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await signOutAndForget(request)).toBe(false);
    await settle();
    expect(tab.heard).toEqual([{ kind: "forget", from: expect.any(String) }]);
  });

  it.each(["forget", "signed_out"] as const)(
    "forgets this tab's key and chat when another tab says %s",
    async (kind) => {
      const news = listen();
      otherTab().post({ kind, from: "another-tab" });
      await settle();
      expect(news).toEqual([kind]);
      expectForgotten();
    }
  );

  it("ignores its own announcements", async () => {
    const news = listen();
    await signOutAndForget(ok());
    await settle();
    expect(news).toEqual([]);
  });

  it("ignores anything that isn't an announcement", async () => {
    const news = listen();
    const tab = otherTab();
    tab.post("signed_out");
    tab.post(null);
    tab.post({ kind: "bogus", from: "another-tab" });
    tab.post({ kind: "signed_out" });
    await settle();
    expect(news).toEqual([]);
    expect(session.data.has(STORAGE_KEY)).toBe(true);
    expect(local.data.has(TRANSCRIPT_KEY)).toBe(true);
  });

  it("still signs out where BroadcastChannel is missing", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    expect(await signOutAndForget(ok())).toBe(true);
    expectForgotten();
    const stop = listenForSignOut(() => {});
    expect(() => stop()).not.toThrow();
  });
});
