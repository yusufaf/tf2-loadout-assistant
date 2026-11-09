import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ChatHistoryRejectedError,
  createLoadout,
  deleteLoadout,
  fetchCosmetic,
  fetchCosmetics,
  fetchInventory,
  fetchLoadouts,
  fetchMe,
  LoadoutWriteUnconfirmedError,
  lookupCosmetics,
  MAX_COSMETIC_LOOKUP,
  signOut,
  streamChat,
  updateLoadout,
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

describe("fetchCosmetics", () => {
  it("returns the class's items, asking for the whole catalog with a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ items: [{ defindex: 1 }] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchCosmetics("pyro", "")).resolves.toEqual([{ defindex: 1 }]);
    expect(fetchMock).toHaveBeenCalledWith("/cosmetics?used_by=pyro&limit=0", {
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after COSMETICS_TIMEOUT_MS on a request that never answers", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            // No signal: never settle, as the hung request would. Throwing here instead
            // would reject without a bound and pass for the wrong reason.
            const signal = init?.signal;
            if (!signal) return;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(fetchCosmetics("pyro", "")).rejects.toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(30_000);
  });

  it("also bounds a body that stalls after the headers arrive", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url: string, init?: RequestInit) =>
          new Response(
            new ReadableStream({
              start(controller) {
                // Like a real fetch, the request's signal also errors the body stream.
                // Without one the stream never closes, as a stalled download would.
                const signal = init?.signal;
                if (!signal) return;
                signal.addEventListener("abort", () => controller.error(signal.reason));
              },
            })
          )
      )
    );
    await expect(fetchCosmetics("pyro", "")).rejects.toHaveProperty("name", "TimeoutError");
  });

  it("rejects on a non-2xx rather than reading as an empty catalog", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(fetchCosmetics("pyro", "")).rejects.toThrow("cosmetics 502");
  });
});

describe("fetchCosmetic", () => {
  it("returns the item, asking with a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ defindex: 30065 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchCosmetic(30065)).resolves.toEqual({ defindex: 30065 });
    expect(fetchMock).toHaveBeenCalledWith("/cosmetics/30065", {
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after COSMETIC_ITEM_TIMEOUT_MS on a request that never answers", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            // No signal: never settle, as the hung request would. Throwing here instead
            // would reject without a bound and pass for the wrong reason.
            const signal = init?.signal;
            if (!signal) return;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(fetchCosmetic(30065)).rejects.toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it("also bounds a body that stalls after the headers arrive", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url: string, init?: RequestInit) =>
          new Response(
            new ReadableStream({
              start(controller) {
                // Like a real fetch, the request's signal also errors the body stream.
                // Without one the stream never closes, as a stalled download would.
                const signal = init?.signal;
                if (!signal) return;
                signal.addEventListener("abort", () => controller.error(signal.reason));
              },
            })
          )
      )
    );
    await expect(fetchCosmetic(30065)).rejects.toHaveProperty("name", "TimeoutError");
  });

  it("rejects on a non-2xx, so the advisor drops that chip", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    await expect(fetchCosmetic(30065)).rejects.toThrow("cosmetic 404");
  });
});

describe("lookupCosmetics", () => {
  it("resolves items and missing, asking with a timeout signal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ items: [{ defindex: 1 }], missing: [2] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(lookupCosmetics([1, 2, 2])).resolves.toEqual({
      items: [{ defindex: 1 }],
      missing: [2],
    });
    expect(fetchMock).toHaveBeenCalledWith("/cosmetics/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ defindexes: [1, 2] }),
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after LOOKUP_TIMEOUT_MS on a request that never answers", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            // No signal: never settle, as the hung request would. Throwing here instead
            // would reject without a bound and pass for the wrong reason.
            const signal = init?.signal;
            if (!signal) return;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(lookupCosmetics([1])).rejects.toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(30_000);
  });

  it("also bounds a body that stalls after the headers arrive", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url: string, init?: RequestInit) =>
          new Response(
            new ReadableStream({
              start(controller) {
                // Like a real fetch, the request's signal also errors the body stream.
                // Without one the stream never closes, as a stalled download would.
                const signal = init?.signal;
                if (!signal) return;
                signal.addEventListener("abort", () => controller.error(signal.reason));
              },
            })
          )
      )
    );
    await expect(lookupCosmetics([1])).rejects.toHaveProperty("name", "TimeoutError");
  });

  it("fails the whole lookup when one chunk hangs, rather than returning the chunks that answered", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      const chunk: number[] = JSON.parse(init!.body as string).defindexes;
      if (!chunk.includes(MAX_COSMETIC_LOOKUP + 1)) {
        return Promise.resolve(
          Response.json({ items: chunk.map((defindex) => ({ defindex })), missing: [] })
        );
      }
      return new Promise<Response>((_, reject) => {
        // No signal: never settle, as the hung request would. Throwing here instead
        // would reject without a bound and pass for the wrong reason.
        const signal = init?.signal;
        if (!signal) return;
        if (signal.aborted) reject(signal.reason);
        signal.addEventListener("abort", () => reject(signal.reason));
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const all = Array.from({ length: MAX_COSMETIC_LOOKUP + 1 }, (_, i) => i + 1);
    await expect(lookupCosmetics(all)).rejects.toHaveProperty("name", "TimeoutError");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects on a non-2xx rather than reading as all missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(lookupCosmetics([1])).rejects.toThrow("lookup 502");
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

  it("reads a probe that never answers as unknown after AUTH_ME_TIMEOUT_MS", async () => {
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
    await expect(fetchMe()).resolves.toBeNull();
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it("reads a request that never reaches the API as unknown", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchMe()).resolves.toBeNull();
  });

  it("reads a non-2xx as unknown, including a 503 that isn't the API's own", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(fetchMe()).resolves.toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Service Unavailable", { status: 503 }))
    );
    await expect(fetchMe()).resolves.toBeNull();
  });

  it("reads a 2xx that isn't a sign-in answer as unknown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>portal</html>", { status: 200 }))
    );
    await expect(fetchMe()).resolves.toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
    await expect(fetchMe()).resolves.toBeNull();
  });

  it("passes a real signed-out answer through", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ signed_in: false })));
    await expect(fetchMe()).resolves.toEqual({ signed_in: false });
  });

  it("reads the API's auth-not-configured 503 as signed out, since nobody can be", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ detail: "auth service unavailable" }, { status: 503 })
        )
    );
    await expect(fetchMe()).resolves.toEqual({ signed_in: false });
  });
});

describe("fetchLoadouts", () => {
  it("returns the account's loadouts, asking with a timeout signal", async () => {
    const record = {
      id: "a",
      name: "Cop",
      cls: "soldier",
      defindexes: [30065],
      created_at: 1,
      updated_at: 1,
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ loadouts: [record] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchLoadouts()).resolves.toEqual([record]);
    expect(fetchMock).toHaveBeenCalledWith("/me/loadouts", { signal: expect.any(AbortSignal) });
  });

  it("reads a request that never answers as a failed read after LOADOUTS_TIMEOUT_MS, never as zero loadouts", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_, reject) => {
            // No signal: never settle, as the hung request would. Throwing here instead
            // would land in fetchLoadouts' catch and pass without a bound.
            const signal = init?.signal;
            if (!signal) return;
            if (signal.aborted) reject(signal.reason);
            signal.addEventListener("abort", () => reject(signal.reason));
          })
      )
    );
    await expect(fetchLoadouts()).resolves.toBeNull();
    expect(timeout).toHaveBeenCalledWith(30_000);
  });

  it("also bounds a body that stalls after the headers arrive", async () => {
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url: string, init?: RequestInit) =>
          new Response(
            new ReadableStream({
              start(controller) {
                // Like a real fetch, the request's signal also errors the body stream.
                // Without one the stream never closes, as a stalled download would.
                const signal = init?.signal;
                if (!signal) return;
                signal.addEventListener("abort", () => controller.error(signal.reason));
              },
            })
          )
      )
    );
    await expect(fetchLoadouts()).resolves.toBeNull();
  });
});

// The same never-answers and stalled-body stubs as above, shared by the three writes.
function stubHungFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_, reject) => {
          // No signal: never settle, as the hung request would. Throwing here instead
          // would land in the write's catch and pass without a bound.
          const signal = init?.signal;
          if (!signal) return;
          if (signal.aborted) reject(signal.reason);
          signal.addEventListener("abort", () => reject(signal.reason));
        })
    )
  );
}

function stubStalledBody() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (_url: string, init?: RequestInit) =>
        new Response(
          new ReadableStream({
            start(controller) {
              // Like a real fetch, the request's signal also errors the body stream.
              // Without one the stream never closes, as a stalled download would.
              const signal = init?.signal;
              if (!signal) return;
              signal.addEventListener("abort", () => controller.error(signal.reason));
            },
          })
        )
    )
  );
}

function shortenTimeouts() {
  const realTimeout = AbortSignal.timeout.bind(AbortSignal);
  return vi.spyOn(AbortSignal, "timeout").mockImplementation(() => realTimeout(5));
}

const record = {
  id: "a",
  name: "Cop",
  cls: "soldier",
  defindexes: [30065],
  created_at: 1,
  updated_at: 1,
};

describe("createLoadout", () => {
  it("returns the stored record, sending it with a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(record));
    vi.stubGlobal("fetch", fetchMock);
    await expect(createLoadout("Cop", "soldier", [30065])).resolves.toEqual(record);
    expect(fetchMock).toHaveBeenCalledWith("/me/loadouts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Cop", cls: "soldier", defindexes: [30065] }),
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after LOADOUT_WRITE_TIMEOUT_MS on a create that never answers, as unconfirmed", async () => {
    const timeout = shortenTimeouts();
    stubHungFetch();
    const err = await createLoadout("Cop", "soldier", [30065]).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it("also reads a body that stalls after a 2xx as unconfirmed", async () => {
    shortenTimeouts();
    stubStalledBody();
    const err = await createLoadout("Cop", "soldier", [30065]).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toHaveProperty("name", "TimeoutError");
  });

  it("reads a request that never reaches the API as unconfirmed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await createLoadout("Cop", "soldier", [30065]).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toBeInstanceOf(TypeError);
  });

  it("rejects a 4xx as a plain failure, since the server refused it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 422 })));
    const err = await createLoadout("Cop", "soldier", [30065]).catch((e) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe("loadouts 422");
    expect(err).not.toBeInstanceOf(LoadoutWriteUnconfirmedError);
  });

  it("reads a 5xx as unconfirmed, since the row may have been written before the error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    const err = await createLoadout("Cop", "soldier", [30065]).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.message).toBe("loadouts 502");
  });
});

describe("updateLoadout", () => {
  it("returns the updated record, sending the patch with a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(record));
    vi.stubGlobal("fetch", fetchMock);
    await expect(updateLoadout("a", { name: "Cop" })).resolves.toEqual(record);
    expect(fetchMock).toHaveBeenCalledWith("/me/loadouts/a", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Cop" }),
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after LOADOUT_WRITE_TIMEOUT_MS on a rename that never answers, as unconfirmed", async () => {
    const timeout = shortenTimeouts();
    stubHungFetch();
    const err = await updateLoadout("a", { name: "Cop" }).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it("also reads a body that stalls after a 2xx as unconfirmed", async () => {
    shortenTimeouts();
    stubStalledBody();
    const err = await updateLoadout("a", { name: "Cop" }).catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toHaveProperty("name", "TimeoutError");
  });

  it("rejects a non-2xx as a plain failure, since retrying a rename is safe", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    const err = await updateLoadout("a", { name: "Cop" }).catch((e) => e);
    expect(err.message).toBe("loadouts 502");
    expect(err).not.toBeInstanceOf(LoadoutWriteUnconfirmedError);
  });
});

describe("deleteLoadout", () => {
  it("resolves on a 204, sending the delete with a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(deleteLoadout("a")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/me/loadouts/a", {
      method: "DELETE",
      signal: expect.any(AbortSignal),
    });
  });

  it("gives up after LOADOUT_WRITE_TIMEOUT_MS on a delete that never answers, as unconfirmed", async () => {
    const timeout = shortenTimeouts();
    stubHungFetch();
    const err = await deleteLoadout("a").catch((e) => e);
    expect(err).toBeInstanceOf(LoadoutWriteUnconfirmedError);
    expect(err.cause).toHaveProperty("name", "TimeoutError");
    expect(timeout).toHaveBeenCalledWith(10_000);
  });

  it("treats a 404 as already deleted", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    await expect(deleteLoadout("a")).resolves.toBeUndefined();
  });

  it("rejects any other non-2xx as a plain failure, since retrying a delete is safe", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })));
    const err = await deleteLoadout("a").catch((e) => e);
    expect(err.message).toBe("loadouts 500");
    expect(err).not.toBeInstanceOf(LoadoutWriteUnconfirmedError);
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
