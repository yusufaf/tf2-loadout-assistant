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
 *
 * It does hold the advisor's tool results, which can list a player's backpack, so it is
 * stamped with the SteamID the server says each turn ran as (`steam_id` on each reply)
 * -- never the page's own idea of who is signed in, which `fetchMe` folds to signed-out
 * on any API failure while the cookie still works. Only that viewer reads it back; a
 * transcript made entirely signed out (owner null) is readable by anyone, so it carries
 * over on sign-in. One slot per browser: another
 * viewer sees an empty panel and leaves the stored copy alone until their own first
 * exchange overwrites it. Only an explicit New chat or Sign out removes it, never an
 * empty panel, which can't tell "cleared" from "loaded for someone else".
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
  /** SteamID the turns in `history` ran as, or null if all ran signed out. */
  owner: string | null;
}

export const EMPTY_TRANSCRIPT: Transcript = { turns: [], history: [], owner: null };

/** Owner of a history made under more than one account. Not a SteamID, so no viewer
 * could read it back, and it is never saved. */
export const MIXED_OWNER = "mixed";

export const TRANSCRIPT_KEY = "tf2-chat/v1";

/** 20 exchanges. Even, so trimming the oldest never splits a question from its reply. */
export const MAX_TURNS = 40;

export const DEFAULT_FAILURE = "The advisor didn't answer. Try again?";

export function appendExchange(
  t: Transcript,
  userText: string,
  bot: Turn,
  history: unknown[],
  ranAs: string | null = null
): Transcript {
  return {
    turns: [...t.turns, { role: "user" as const, text: userText }, bot].slice(-MAX_TURNS),
    history,
    owner: mergeOwner(t.owner, ranAs),
  };
}

/** Never back to null, and never over to another player: the server's history still
 * holds the earlier turns either way. */
function mergeOwner(prev: string | null, seen: string | null): string | null {
  if (seen === null || seen === prev) return prev;
  return prev === null ? seen : MIXED_OWNER;
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
  owner: string | null;
  turns: Turn[];
  history: unknown[];
}

export function serializeTranscript(t: Transcript): string {
  const { owner, turns, history } = t;
  const file: StoredTranscript = { version: 1, owner, turns, history };
  return JSON.stringify(file);
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function readableBy(owner: string | null, viewer: string | null): boolean {
  return owner === null || owner === viewer;
}

type Envelope = Record<string, unknown> & { owner: string | null };

/** The stored envelope, or null when nothing valid is stored. */
function readEnvelope(raw: string | null): Envelope | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isObject(parsed) || parsed.version !== 1) return null;
  const owner = parsed.owner;
  if (owner !== null && typeof owner !== "string") return null;
  return { ...parsed, owner };
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
export function parseTranscript(raw: string | null, viewer: string | null): Transcript {
  const file = readEnvelope(raw);
  if (!file || !readableBy(file.owner, viewer)) return EMPTY_TRANSCRIPT;
  if (!Array.isArray(file.history) || !Array.isArray(file.turns)) {
    return EMPTY_TRANSCRIPT;
  }
  const turns: Turn[] = [];
  for (const raw of file.turns) {
    const turn = parseTurn(raw);
    if (!turn) return EMPTY_TRANSCRIPT;
    turns.push(turn);
  }
  return { turns: turns.slice(-MAX_TURNS), history: file.history, owner: file.owner };
}

export function loadTranscript(viewer: string | null): Transcript {
  try {
    return parseTranscript(localStorage.getItem(TRANSCRIPT_KEY), viewer);
  } catch {
    return EMPTY_TRANSCRIPT;
  }
}

/** New chat and Sign out. */
export function clearTranscript(): void {
  try {
    localStorage.removeItem(TRANSCRIPT_KEY);
  } catch {
    // Storage is unusable; there is nothing saved to clear.
  }
}

/** Saving nothing is a no-op: an empty panel may have been loaded for another viewer,
 * and their conversation is still in the slot. */
export function saveTranscript(t: Transcript): void {
  if (t.turns.length === 0) return;
  if (t.owner === MIXED_OWNER) {
    clearTranscript();
    return;
  }
  try {
    localStorage.setItem(TRANSCRIPT_KEY, serializeTranscript(t));
  } catch {
    // Quota exceeded: an older conversation coming back on reload would be worse
    // than none, and this one would have replaced it anyway.
    clearTranscript();
  }
}
