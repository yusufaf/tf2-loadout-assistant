/**
 * The advisor conversation's rules, outside `ChatPanel` so they test without a DOM.
 *
 * A finished turn either becomes an exchange in the transcript or hands the player
 * their message back: a user message never sits in the log unless the server
 * accepted that exchange. The server trims `history` to its newest turns that fit its
 * caps, so the log can show more than the advisor still remembers; `MAX_TURNS` only
 * bounds what this browser keeps.
 *
 * The transcript is kept in this browser with the same conventions as `llmKey.ts`: a
 * versioned envelope, and every storage call wrapped so private mode or a full quota
 * degrades to "no saved conversation". It holds only what the server already saw;
 * the key is never in it.
 */

import {
  ChatHistoryRejectedError,
  ChatUnavailableError,
  LlmKeyError,
  RateLimitedError,
  type ChatStreamEvent,
  type Conflict,
  type Cosmetic,
} from "./api";
import { CLASSES } from "./classes";
import { parseCosmetic } from "./savedLoadouts";

export interface Turn {
  role: "user" | "bot";
  text: string;
  /** Bot only: the class the question was asked for. */
  cls?: string;
  /** Items the agent recommended, already resolved against the real catalog. */
  suggestions?: Cosmetic[];
  /** Clashes within the suggested set, server-checked even if the agent skipped it. */
  conflicts?: Conflict[];
}

export interface Transcript {
  turns: Turn[];
  /** Opaque server state, handed straight back each turn. */
  history: unknown[];
}

export const EMPTY_TRANSCRIPT: Transcript = { turns: [], history: [] };

export const TRANSCRIPT_KEY = "tf2-chat/v1";

/** 20 exchanges. Even, so trimming the oldest never splits a question from its reply. */
export const MAX_TURNS = 40;

export const DEFAULT_FAILURE = "The advisor didn't answer. Try again?";

export function appendExchange(
  t: Transcript,
  userText: string,
  bot: Turn,
  history: unknown[]
): Transcript {
  return {
    turns: [...t.turns, { role: "user" as const, text: userText }, bot].slice(-MAX_TURNS),
    history,
  };
}

export type TurnOutcome =
  | { kind: "reply"; reply: ChatStreamEvent }
  | { kind: "bad_key" }
  | { kind: "stopped" }
  | { kind: "failed"; message: string };

/** How a turn ended, from everything the stream produced. A turn that reached `final`
 * is in the server's history, so it is kept even if Stop was pressed afterwards. */
export function turnOutcome(r: {
  final: ChatStreamEvent | null;
  errorEvent: ChatStreamEvent | null;
  thrown: unknown;
  aborted: boolean;
}): TurnOutcome {
  if (r.errorEvent) {
    return r.errorEvent.code === "bad_key"
      ? { kind: "bad_key" }
      : { kind: "failed", message: r.errorEvent.detail ?? DEFAULT_FAILURE };
  }
  if (r.final) return { kind: "reply", reply: r.final };
  if (r.aborted) return { kind: "stopped" };
  const err = r.thrown;
  if (err instanceof LlmKeyError) return { kind: "bad_key" };
  if (err instanceof ChatUnavailableError) {
    return { kind: "failed", message: "Chat is switched off on the server right now." };
  }
  if (err instanceof ChatHistoryRejectedError) {
    return {
      kind: "failed",
      message: "The advisor couldn't pick up this conversation. Start a new chat and try again.",
    };
  }
  if (err instanceof RateLimitedError) {
    return {
      kind: "failed",
      message: err.retryAfterSeconds
        ? `You're sending messages faster than the advisor allows. Try again in ${err.retryAfterSeconds} seconds.`
        : "The advisor is busy with too many messages at once. Wait a moment and try again.",
    };
  }
  return { kind: "failed", message: DEFAULT_FAILURE };
}

interface StoredTranscript {
  version: 1;
  turns: Turn[];
  history: unknown[];
}

export function serializeTranscript(t: Transcript): string {
  const file: StoredTranscript = { version: 1, turns: t.turns, history: t.history };
  return JSON.stringify(file);
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function parseConflict(v: unknown): Conflict | null {
  if (!isObject(v)) return null;
  if (typeof v.a !== "number" || typeof v.b !== "number") return null;
  if (!Array.isArray(v.regions) || !v.regions.every((r) => typeof r === "string")) {
    return null;
  }
  return { a: v.a, b: v.b, regions: v.regions };
}

function parseTurn(v: unknown): Turn | null {
  if (!isObject(v)) return null;
  if (v.role !== "user" && v.role !== "bot") return null;
  if (typeof v.text !== "string") return null;
  const turn: Turn = { role: v.role, text: v.text };
  if (typeof v.cls === "string" && CLASSES.includes(v.cls)) turn.cls = v.cls;
  if (Array.isArray(v.suggestions)) {
    turn.suggestions = v.suggestions
      .map(parseCosmetic)
      .filter((c): c is Cosmetic => c !== null);
  }
  if (Array.isArray(v.conflicts)) {
    turn.conflicts = v.conflicts
      .map(parseConflict)
      .filter((c): c is Conflict => c !== null);
  }
  return turn;
}

/** All-or-nothing on structure: dropping one malformed turn would leave a question
 * without its reply. Lenient within a turn, like `savedLoadouts`' imports. */
export function parseTranscript(raw: string | null): Transcript {
  if (!raw) return EMPTY_TRANSCRIPT;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return EMPTY_TRANSCRIPT;
  }
  if (!isObject(parsed) || parsed.version !== 1) return EMPTY_TRANSCRIPT;
  if (!Array.isArray(parsed.history) || !Array.isArray(parsed.turns)) {
    return EMPTY_TRANSCRIPT;
  }
  const turns: Turn[] = [];
  for (const raw of parsed.turns) {
    const turn = parseTurn(raw);
    if (!turn) return EMPTY_TRANSCRIPT;
    turns.push(turn);
  }
  return { turns: turns.slice(-MAX_TURNS), history: parsed.history };
}

export function loadTranscript(): Transcript {
  try {
    return parseTranscript(localStorage.getItem(TRANSCRIPT_KEY));
  } catch {
    return EMPTY_TRANSCRIPT;
  }
}

export function saveTranscript(t: Transcript): void {
  try {
    if (t.turns.length === 0) {
      localStorage.removeItem(TRANSCRIPT_KEY);
    } else {
      localStorage.setItem(TRANSCRIPT_KEY, serializeTranscript(t));
    }
  } catch {
    // Quota exceeded: an older conversation coming back on reload would be worse
    // than none, so drop the stale copy.
    try {
      localStorage.removeItem(TRANSCRIPT_KEY);
    } catch {
      // Storage is unusable; the conversation still works for this page load.
    }
  }
}
