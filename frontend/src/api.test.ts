import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ChatHistoryRejectedError,
  fetchInventory,
  fetchMe,
  signOut,
  streamChat,
  type ChatStreamEvent,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("signOut", () => {
  it("rejects when the request never reaches the API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(signOut()).rejects.toThrow();
  });

  it("rejects on a non-2xx, since the session cookie wasn't cleared", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(signOut()).rejects.toThrow();
  });

  it("resolves once the API has cleared the session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(signOut()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", {
      method: "POST",
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after SIGN_OUT_TIMEOUT_MS on a request that never answers", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            const signal = init!.signal!;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(signOut()).rejects.toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(10_000);
  });
});

describe("fetchMe", () => {
  it("returns the session the API reports, asking with a timeout signal", async () => {
    const body = {
      signed_in: true,
      steam_id: "76561197960287930",
      persona: "p",
      avatar: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(body));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchMe()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith("/auth/me", { signal: expect.any(AbortSignal) });
  });

  it("reads a probe that never answers as signed out after AUTH_ME_TIMEOUT_MS", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            // No signal: never settle, as the hung request would. Throwing here instead
            // would land in fetchMe's catch and pass without a bound.
            const signal = init?.signal;
            if (!signal) return;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(fetchMe()).resolves.toEqual({ signed_in: false });
    expect(timeout).toHaveBeenCalledWith(10_000);
  });
});

describe("fetchInventory", () => {
  it("asks the server to bypass its cache on refresh", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", defindexes: [], fetched_at: 1 }))
    );
    vi.stubGlobal("fetch", fetchMock);
    await fetchInventory(true);
    expect(fetchMock).toHaveBeenCalledWith("/me/inventory?refresh=1");
  });
});

describe("streamChat", () => {
  const llm = { provider: "anthropic", apiKey: "k" };

  it("passes the abort signal to fetch", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('{"kind":"final","message":"hi"}\n'));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await streamChat("m", [], [], llm, () => {}, controller.signal);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("reassembles a line split across chunks", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode('{"kind":"tool","name":"x"}\n{"ki'));
        c.enqueue(encoder.encode('nd":"final"}'));
        c.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const events: ChatStreamEvent[] = [];
    await streamChat("m", [], [], llm, (e) => events.push(e));
    expect(events).toEqual([{ kind: "tool", name: "x" }, { kind: "final" }]);
  });

  it("throws ChatHistoryRejectedError on the server's history 422", async () => {
    const res = Response.json({ detail: "malformed chat history" }, { status: 422 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
    await expect(streamChat("m", [{ bad: true }], [], llm, () => {})).rejects.toBeInstanceOf(
      ChatHistoryRejectedError
    );
  });

  it("treats a request-body 422 as a plain failure, not a stale history", async () => {
    const res = Response.json({ detail: [{ loc: ["body", "message"] }] }, { status: 422 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
    const err = await streamChat("m", [], [], llm, () => {}).catch((e) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ChatHistoryRejectedError);
  });
});
