import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ChatHistoryRejectedError,
  fetchInventory,
  signOut,
  streamChat,
  type ChatStreamEvent,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

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
    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", { method: "POST" });
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
