import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TRANSCRIPT_KEY, serializeTranscript } from "./chatSession";
import { STORAGE_KEY, loadLlmKey, saveLlmKey, serializeKey } from "./llmKey";
import {
  SIGN_OUT_CHANNEL,
  SIGN_OUT_MARK_KEY,
  catchUpWithSignOut,
  listenForMissedSignOut,
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
      { kind: "forget", from: expect.any(String), mark: expect.any(String) },
      { kind: "signed_out", from: expect.any(String), mark: expect.any(String) },
    ]);
  });

  it("tells them to forget even when the logout request fails", async () => {
    const tab = otherTab();
    const request = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await signOutAndForget(request)).toBe(false);
    await settle();
    expect(tab.heard).toEqual([
      { kind: "forget", from: expect.any(String), mark: expect.any(String) },
    ]);
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

const K = { provider: "anthropic", apiKey: "sk-abc" };

/** Points `sessionStorage` at one tab's own store; `localStorage` stays shared. */
function swapTab(store: ReturnType<typeof fakeStorage>) {
  vi.stubGlobal("sessionStorage", store);
}

describe("tabs that missed it", () => {
  it("a tab reopened after another tab signed out forgets its tab-only key", async () => {
    const tabA = fakeStorage();
    const tabB = fakeStorage();
    swapTab(tabA);
    saveLlmKey(K, false);
    swapTab(tabB);
    await signOutAndForget(ok());
    swapTab(tabA);
    expect(catchUpWithSignOut()).toBe("signed_out");
    expect(loadLlmKey()).toBeNull();
  });

  it("keeps a key and chat the next person saved", async () => {
    const tabA = fakeStorage();
    const tabB = fakeStorage();
    swapTab(tabA);
    saveLlmKey(K, false);
    swapTab(tabB);
    await signOutAndForget(ok());
    saveLlmKey({ provider: "openai", apiKey: "sk-next" }, true);
    local.setItem(TRANSCRIPT_KEY, TRANSCRIPT);
    swapTab(tabA);
    expect(catchUpWithSignOut()).toBe("signed_out");
    expect(loadLlmKey()).toEqual({ provider: "openai", apiKey: "sk-next", remember: true });
    expect(local.data.has(TRANSCRIPT_KEY)).toBe(true);
  });

  it("the signing-out tab is already caught up", async () => {
    await signOutAndForget(ok());
    saveLlmKey(K, false);
    expect(catchUpWithSignOut()).toBeNull();
    expect(loadLlmKey()).toEqual({ ...K, remember: false });
  });

  it("a tab that heard the broadcast records the mark it carried", async () => {
    // The sender's localStorage write may not be visible here yet when the news lands.
    local.setItem(SIGN_OUT_MARK_KEY, "m-old");
    listen();
    otherTab().post({ kind: "forget", from: "another-tab", mark: "m-new" });
    await settle();
    local.setItem(SIGN_OUT_MARK_KEY, "m-new");
    saveLlmKey(K, false);
    expect(catchUpWithSignOut()).toBeNull();
    expect(loadLlmKey()).toEqual({ ...K, remember: false });
  });

  it("each announcement carries the mark current when it was posted", async () => {
    const tab = otherTab();
    let m1: string | null = null;
    await signOutAndForget(async () => {
      m1 = local.getItem(SIGN_OUT_MARK_KEY);
    });
    await settle();
    const [forgot, signedOut] = tab.heard as Array<{ mark?: string }>;
    expect(m1).toBeTruthy();
    expect(forgot.mark).toBe(m1);
    expect(signedOut.mark).toBe(local.getItem(SIGN_OUT_MARK_KEY));
    expect(signedOut.mark).not.toBe(m1);
  });

  it("nothing to catch up on in a browser that never signed out", () => {
    saveLlmKey(K, false);
    expect(catchUpWithSignOut()).toBeNull();
    expect(loadLlmKey()).toEqual({ ...K, remember: false });
  });

  it("counts unreadable storage as missed", () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { ...local, getItem: blocked });
    expect(catchUpWithSignOut()).toBe("unreadable");
    expect(session.data.has(STORAGE_KEY)).toBe(false);

    session.setItem(STORAGE_KEY, KEY);
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", { ...session, getItem: blocked });
    expect(catchUpWithSignOut()).toBe("unreadable");
    expect(session.data.has(STORAGE_KEY)).toBe(false);
  });

  it("a sign-out whose mark can't be saved still signs out", async () => {
    vi.stubGlobal("localStorage", {
      ...local,
      setItem: () => {
        throw new DOMException("full", "QuotaExceededError");
      },
    });
    const tab = otherTab();
    expect(await signOutAndForget(ok())).toBe(true);
    expectForgotten();
    await settle();
    expect(tab.heard).toHaveLength(2);
    for (const m of tab.heard) expect(m).not.toHaveProperty("mark");
  });

  it("writes the mark after clearing the transcript", async () => {
    vi.stubGlobal("localStorage", {
      ...local,
      setItem: (k: string, v: string) => {
        if (local.data.has(TRANSCRIPT_KEY)) throw new DOMException("full", "QuotaExceededError");
        local.setItem(k, v);
      },
    });
    await signOutAndForget(ok());
    expect(local.data.has(SIGN_OUT_MARK_KEY)).toBe(true);
  });

  it("a tab-only key saved during the request is caught up on", async () => {
    const tabA = fakeStorage();
    const tabB = fakeStorage();
    swapTab(tabB);
    await signOutAndForget(async () => {
      // Tab A heard "forget" (recording its mark, as its listener does), then saved a
      // key; it never hears "signed_out".
      const m1 = local.getItem(SIGN_OUT_MARK_KEY);
      swapTab(tabA);
      tabA.setItem(SIGN_OUT_MARK_KEY, m1!);
      saveLlmKey(K, false);
      swapTab(tabB);
    });
    swapTab(tabA);
    expect(catchUpWithSignOut()).toBe("signed_out");
    expect(loadLlmKey()).toBeNull();
  });
});

describe("listenForMissedSignOut", () => {
  it("calls back only for a bfcache restore after a missed sign-out", () => {
    const win = new EventTarget();
    vi.stubGlobal("window", win);
    local.setItem(SIGN_OUT_MARK_KEY, "m1");
    saveLlmKey(K, false);
    const calls: string[] = [];
    open.push(listenForMissedSignOut((why) => calls.push(why)));
    const fire = (persisted: boolean) =>
      win.dispatchEvent(Object.assign(new Event("pageshow"), { persisted }));

    fire(false);
    expect(calls).toEqual([]);
    expect(loadLlmKey()).toEqual({ ...K, remember: false });

    fire(true);
    expect(calls).toEqual(["signed_out"]);
    expect(session.data.has(STORAGE_KEY)).toBe(false);

    fire(true);
    expect(calls).toEqual(["signed_out"]);
  });

  it("is a no-op without window", () => {
    const stop = listenForMissedSignOut(() => {});
    expect(() => stop()).not.toThrow();
  });
});
