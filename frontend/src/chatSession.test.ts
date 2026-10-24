import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ChatHistoryRejectedError,
  ChatUnavailableError,
  LlmKeyError,
  RateLimitedError,
  type ChatStreamEvent,
  type Cosmetic,
} from "./api";
import {
  DEFAULT_FAILURE,
  EMPTY_TRANSCRIPT,
  MAX_TURNS,
  TRANSCRIPT_KEY,
  appendExchange,
  clearTranscript,
  loadTranscript,
  parseTranscript,
  saveTranscript,
  serializeTranscript,
  turnOutcome,
  type Transcript,
} from "./chatSession";

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

const A = "76561190000000001";
const B = "76561190000000002";

const HAT: Cosmetic = {
  defindex: 1,
  name: "Fancy Fedora",
  equip_regions: ["hat"],
  used_by_classes: ["Spy"],
  item_slot: "head",
  image_url: "https://example.com/fedora.png",
  price: null,
  paintable: false,
  holiday_restriction: null,
  styles: [],
};

const EXCHANGE: Transcript = {
  turns: [
    { role: "user", text: "cop look" },
    {
      role: "bot",
      text: "Try this",
      cls: "Spy",
      suggestions: [HAT],
      conflicts: [{ a: 1, b: 2, regions: ["hat"] }],
    },
  ],
  history: [{ kind: "request" }, { kind: "response" }],
};

const FINAL: ChatStreamEvent = { kind: "final", message: "hi", history: [] };
const NOTHING = { final: null, errorEvent: null, thrown: undefined, aborted: false };

describe("appendExchange", () => {
  it("adds the user message and reply together and takes the server's history", () => {
    const next = appendExchange(EMPTY_TRANSCRIPT, "q", { role: "bot", text: "a" }, ["h"]);
    expect(next).toEqual({
      turns: [
        { role: "user", text: "q" },
        { role: "bot", text: "a" },
      ],
      history: ["h"],
    });
  });

  it("keeps only the newest MAX_TURNS turns, whole exchanges", () => {
    let t = EMPTY_TRANSCRIPT;
    for (let i = 0; i < MAX_TURNS / 2 + 3; i++) {
      t = appendExchange(t, `q${i}`, { role: "bot", text: `a${i}` }, []);
    }
    expect(t.turns).toHaveLength(MAX_TURNS);
    expect(t.turns[0]).toEqual({ role: "user", text: "q3" });
    expect(t.turns.at(-1)).toEqual({ role: "bot", text: `a${MAX_TURNS / 2 + 2}` });
  });
});

describe("turnOutcome", () => {
  it("a final reply is a reply", () => {
    expect(turnOutcome({ ...NOTHING, final: FINAL })).toEqual({ kind: "reply", reply: FINAL });
  });

  it("a final reply wins even if Stop was pressed after it arrived", () => {
    const thrown = new DOMException("aborted", "AbortError");
    expect(turnOutcome({ ...NOTHING, final: FINAL, thrown, aborted: true }).kind).toBe(
      "reply"
    );
  });

  it("an abort with no reply is stopped, not an error", () => {
    const thrown = new DOMException("aborted", "AbortError");
    expect(turnOutcome({ ...NOTHING, thrown, aborted: true })).toEqual({ kind: "stopped" });
  });

  it("a bad_key error event is bad_key, not a failure", () => {
    const errorEvent: ChatStreamEvent = { kind: "error", code: "bad_key", detail: "401" };
    expect(turnOutcome({ ...NOTHING, errorEvent })).toEqual({ kind: "bad_key" });
  });

  it("any other error event fails with its detail, or the default", () => {
    expect(
      turnOutcome({ ...NOTHING, errorEvent: { kind: "error", detail: "model fell over" } })
    ).toEqual({ kind: "failed", message: "model fell over" });
    expect(turnOutcome({ ...NOTHING, errorEvent: { kind: "error" } })).toEqual({
      kind: "failed",
      message: DEFAULT_FAILURE,
    });
  });

  it("LlmKeyError thrown before the stream is bad_key", () => {
    expect(turnOutcome({ ...NOTHING, thrown: new LlmKeyError("401") })).toEqual({
      kind: "bad_key",
    });
  });

  it("chat switched off on the server says so", () => {
    expect(turnOutcome({ ...NOTHING, thrown: new ChatUnavailableError("503") })).toEqual({
      kind: "failed",
      message: "Chat is switched off on the server right now.",
    });
  });

  it("a 422 tells the player to start a new chat", () => {
    const outcome = turnOutcome({ ...NOTHING, thrown: new ChatHistoryRejectedError("422") });
    expect(outcome.kind).toBe("failed");
    expect(outcome.kind === "failed" && outcome.message).toMatch(/new chat/);
  });

  it("rate limited with and without Retry-After", () => {
    const timed = turnOutcome({ ...NOTHING, thrown: new RateLimitedError(30) });
    expect(timed.kind === "failed" && timed.message).toMatch(/30 seconds/);
    const busy = turnOutcome({ ...NOTHING, thrown: new RateLimitedError(null) });
    expect(busy.kind === "failed" && busy.message).toMatch(/too many messages at once/);
  });

  it("no final and nothing thrown is the default failure", () => {
    expect(turnOutcome(NOTHING)).toEqual({ kind: "failed", message: DEFAULT_FAILURE });
    expect(turnOutcome({ ...NOTHING, thrown: new TypeError("Failed to fetch") })).toEqual({
      kind: "failed",
      message: DEFAULT_FAILURE,
    });
  });
});

describe("parseTranscript", () => {
  it("round-trips through serializeTranscript", () => {
    expect(parseTranscript(serializeTranscript(EXCHANGE, A), A)).toEqual(EXCHANGE);
    expect(parseTranscript(serializeTranscript(EXCHANGE, null), null)).toEqual(EXCHANGE);
  });

  it("returns empty for nothing stored, garbage, or the wrong version", () => {
    expect(parseTranscript(null, A)).toEqual(EMPTY_TRANSCRIPT);
    expect(parseTranscript("not json", A)).toEqual(EMPTY_TRANSCRIPT);
    expect(
      parseTranscript(JSON.stringify({ version: 3, owner: A, turns: [], history: [] }), A)
    ).toEqual(EMPTY_TRANSCRIPT);
    expect(parseTranscript(JSON.stringify({ version: 2, owner: A, turns: [] }), A)).toEqual(
      EMPTY_TRANSCRIPT
    );
  });

  it("reads a v1 transcript, which has no owner, as empty", () => {
    const raw = JSON.stringify({ version: 1, ...EXCHANGE });
    expect(parseTranscript(raw, A)).toEqual(EMPTY_TRANSCRIPT);
    expect(parseTranscript(raw, null)).toEqual(EMPTY_TRANSCRIPT);
  });

  it("hides another identity's transcript", () => {
    const mine = serializeTranscript(EXCHANGE, A);
    expect(parseTranscript(mine, B)).toEqual(EMPTY_TRANSCRIPT);
    expect(parseTranscript(mine, null)).toEqual(EMPTY_TRANSCRIPT);
    expect(parseTranscript(serializeTranscript(EXCHANGE, null), A)).toEqual(EMPTY_TRANSCRIPT);
  });

  it("rejects the whole transcript if any turn is malformed", () => {
    const raw = JSON.stringify({
      version: 2,
      owner: A,
      turns: [...EXCHANGE.turns, { role: "system", text: "x" }],
      history: [],
    });
    expect(parseTranscript(raw, A)).toEqual(EMPTY_TRANSCRIPT);
  });

  it("drops an unknown class and malformed suggestions but keeps the turn", () => {
    const raw = JSON.stringify({
      version: 2,
      owner: A,
      turns: [
        { role: "user", text: "q" },
        {
          role: "bot",
          text: "a",
          cls: "Civilian",
          suggestions: [HAT, { defindex: "nope" }],
          conflicts: [{ a: 1 }, { a: 1, b: 2, regions: ["hat"] }],
        },
      ],
      history: [],
    });
    expect(parseTranscript(raw, A).turns[1]).toEqual({
      role: "bot",
      text: "a",
      suggestions: [HAT],
      conflicts: [{ a: 1, b: 2, regions: ["hat"] }],
    });
  });

  it("upgrades http suggestion images to https", () => {
    const insecure: Transcript = {
      turns: [
        { role: "user", text: "q" },
        { role: "bot", text: "a", suggestions: [{ ...HAT, image_url: "http://x/y.png" }] },
      ],
      history: [],
    };
    const raw = serializeTranscript(insecure, A);
    expect(parseTranscript(raw, A).turns[1].suggestions?.[0].image_url).toBe("https://x/y.png");
  });
});

describe("localStorage wrappers", () => {
  it("save then load round-trips", () => {
    vi.stubGlobal("localStorage", fakeStorage());
    saveTranscript(EXCHANGE, A);
    expect(loadTranscript(A)).toEqual(EXCHANGE);
    expect(loadTranscript(B)).toEqual(EMPTY_TRANSCRIPT);
  });

  it("clearTranscript removes the key", () => {
    const storage = fakeStorage({ [TRANSCRIPT_KEY]: serializeTranscript(EXCHANGE, A) });
    vi.stubGlobal("localStorage", storage);
    clearTranscript();
    expect(storage.data.has(TRANSCRIPT_KEY)).toBe(false);
  });

  it("saving an empty transcript removes the key", () => {
    const storage = fakeStorage({ [TRANSCRIPT_KEY]: serializeTranscript(EXCHANGE, A) });
    vi.stubGlobal("localStorage", storage);
    saveTranscript(EMPTY_TRANSCRIPT, A);
    expect(storage.data.has(TRANSCRIPT_KEY)).toBe(false);
  });

  it("a quota error removes the stale copy", () => {
    const storage = fakeStorage({ [TRANSCRIPT_KEY]: serializeTranscript(EXCHANGE, A) });
    vi.stubGlobal("localStorage", {
      ...storage,
      setItem: () => {
        throw new DOMException("full", "QuotaExceededError");
      },
    });
    saveTranscript(appendExchange(EXCHANGE, "q2", { role: "bot", text: "a2" }, []), A);
    expect(storage.data.has(TRANSCRIPT_KEY)).toBe(false);
  });

  it("a throwing localStorage loads as empty", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("denied");
      },
    });
    expect(loadTranscript(A)).toEqual(EMPTY_TRANSCRIPT);
  });
});
