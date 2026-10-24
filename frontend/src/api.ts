import type { ConflictMatrix } from "./conflicts";
import type { LlmKey } from "./llmKey";

export interface Price {
  currency: string;
  value: number;
  value_high: number | null;
  /** Normalized to refined metal for sort/budget comparisons; null when the currency
   * (hat, usd) has no ref equivalent. */
  ref_value: number | null;
}

export interface Cosmetic {
  defindex: number;
  name: string;
  equip_regions: string[];
  used_by_classes: string[];
  item_slot: string | null;
  image_url: string | null;
  price: Price | null;
  paintable: boolean;
  holiday_restriction: string | null;
  styles: string[];
}

export interface Conflict {
  a: number;
  b: number;
  regions: string[];
}

// Empty string = same-origin: prod serves the built frontend from the API itself, and
// the dev proxy (vite.config.ts) forwards API prefixes to :8000, so both cases share
// one origin -- required for the Steam sign-in cookie (SameSite=Lax) to ride along.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export async function fetchCosmetics(usedBy: string, q: string): Promise<Cosmetic[]> {
  // limit=0 means "everything": filtering happens client-side, so a truncated page
  // would silently hide items the user has filtered down to.
  const params = new URLSearchParams({ used_by: usedBy, limit: "0" });
  if (q) params.set("q", q);
  const res = await fetch(`${BASE}/cosmetics?${params}`);
  if (!res.ok) throw new Error(`cosmetics ${res.status}`);
  return (await res.json()).items;
}

/** The cross-region conflict matrix. Static, so fetch it once and keep it. */
export async function fetchConflictMatrix(): Promise<ConflictMatrix> {
  const res = await fetch(`${BASE}/equip-conflicts`);
  if (!res.ok) throw new Error(`equip-conflicts ${res.status}`);
  return (await res.json()).matrix;
}

export async function fetchCosmetic(defindex: number): Promise<Cosmetic> {
  const res = await fetch(`${BASE}/cosmetics/${defindex}`);
  if (!res.ok) throw new Error(`cosmetic ${res.status}`);
  return res.json();
}

/** Mirrors `MAX_COSMETIC_LOOKUP` in api.py: a bigger POST 422s, so lookups are chunked. */
export const MAX_COSMETIC_LOOKUP = 500;

/** Resolve many defindexes in one request per chunk. `missing` lists the ones the
 * catalog doesn't have; a failed request throws rather than reading as "all missing". */
export async function lookupCosmetics(
  defindexes: number[]
): Promise<{ items: Cosmetic[]; missing: number[] }> {
  const unique = [...new Set(defindexes)];
  if (unique.length === 0) return { items: [], missing: [] };
  const chunks: number[][] = [];
  for (let i = 0; i < unique.length; i += MAX_COSMETIC_LOOKUP) {
    chunks.push(unique.slice(i, i + MAX_COSMETIC_LOOKUP));
  }
  const results = await Promise.all(
    chunks.map(async (chunk) => {
      const res = await fetch(`${BASE}/cosmetics/lookup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ defindexes: chunk }),
      });
      if (!res.ok) throw new Error(`lookup ${res.status}`);
      return (await res.json()) as { items: Cosmetic[]; missing: number[] };
    })
  );
  return {
    items: results.flatMap((r) => r.items),
    missing: results.flatMap((r) => r.missing),
  };
}

export async function fetchConflicts(defindexes: number[]): Promise<Conflict[]> {
  if (defindexes.length < 2) return [];
  const res = await fetch(`${BASE}/loadout/conflicts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ defindexes }),
  });
  if (!res.ok) throw new Error(`conflicts ${res.status}`);
  return (await res.json()).conflicts;
}

/** A turn's worth of chat. `history` is opaque transcript state we hand straight back. */
export interface ChatReply {
  message: string;
  suggested_defindexes: number[];
  conflicts: Conflict[];
  history: unknown[];
}

/** The API is up but the chat service failed to build, so the panel should stay hidden. */
export class ChatUnavailableError extends Error {}

/** The turn never reached the model because the player's key was missing, for a
 * provider the server doesn't know, or rejected by the provider itself (401). The
 * one error the panel answers by reopening the key form. */
export class LlmKeyError extends Error {}

/** The server refused the turn as too frequent (429, with Retry-After), or too many
 * turns were already in flight from this player or the whole site (429, without). */
export class RateLimitedError extends Error {
  readonly retryAfterSeconds: number | null;

  constructor(retryAfterSeconds: number | null) {
    super("rate limited");
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export interface ChatProvider {
  id: string;
  label: string;
  /** The one fixed model this provider is used with; shown, not editable. */
  model: string;
}

/** The dropdown's contents come from the server's allowlist so the two can't drift.
 * `[]` on failure: the form still renders, the player just can't pick anything. */
export async function fetchChatProviders(): Promise<ChatProvider[]> {
  try {
    const res = await fetch(`${BASE}/chat/providers`);
    if (!res.ok) return [];
    return (await res.json()).providers ?? [];
  } catch {
    return [];
  }
}

/** The key travels in headers, never the body: the body is the transcript, which
 * the client keeps and resends, and nothing in it should ever be worth stealing. */
function chatHeaders(llm: LlmKey): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-LLM-Provider": llm.provider,
    "X-LLM-API-Key": llm.apiKey,
  };
}

function throwForChatStatus(res: Response): void {
  if (res.status === 503) throw new ChatUnavailableError("chat not configured");
  if (res.status === 401) throw new LlmKeyError("llm key rejected or missing");
  if (res.status === 429) {
    const retryAfter = Number(res.headers.get("retry-after"));
    throw new RateLimitedError(retryAfter > 0 ? retryAfter : null);
  }
  if (!res.ok) throw new Error(`chat ${res.status}`);
}

export async function fetchChatAvailable(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/healthz`);
    if (!res.ok) return false;
    return Boolean((await res.json()).chat);
  } catch {
    return false;
  }
}

export interface Me {
  signed_in: boolean;
  steam_id?: string;
  persona?: string | null;
  avatar?: string | null;
}

/** Probes sign-in state at mount, same pattern as `fetchChatAvailable`: an absent or
 * unconfigured auth service just means signed-out, never an error the UI surfaces. */
export async function fetchMe(): Promise<Me> {
  try {
    const res = await fetch(`${BASE}/auth/me`);
    if (!res.ok) return { signed_in: false };
    return await res.json();
  } catch {
    return { signed_in: false };
  }
}

export function steamLoginUrl(): string {
  return `${BASE}/auth/steam/login`;
}

/** Throws on a network error or any non-2xx: either way the session cookie wasn't
 * cleared, so the caller must not show the player as signed out. */
export async function signOut(): Promise<void> {
  const res = await fetch(`${BASE}/auth/logout`, { method: "POST" });
  if (!res.ok) throw new Error(`logout ${res.status}`);
}

export type InventoryStatus = "ok" | "private" | "not_found" | "error";

export interface Inventory {
  status: InventoryStatus;
  defindexes: number[];
  fetched_at: number;
}

/** Null means the read failed: signed out (401), refresh rate-limited (429),
 * unconfigured (503), or a network error. The caller shows that as a failed state with
 * a Retry, never as still loading or as owning nothing. */
export async function fetchInventory(refresh = false): Promise<Inventory | null> {
  try {
    const params = refresh ? "?refresh=1" : "";
    const res = await fetch(`${BASE}/me/inventory${params}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export interface LoadoutRecord {
  id: string;
  name: string;
  cls: string;
  defindexes: number[];
  created_at: number;
  updated_at: number;
}

/** The API is up but saved loadouts aren't configured on this deploy (503), so the
 * caller keeps the localStorage store -- not an error the player needs to act on. */
export class LoadoutsUnavailableError extends Error {}

/** Throws `LoadoutsUnavailableError` on the "not configured" 503 (a deploy without
 * DynamoDB, see .env.example); null on any other failure (signed out, network, a
 * transient API error), which `remoteLoadouts.loadAll` turns into a thrown load
 * failure the UI shows with a Retry. */
export async function fetchLoadouts(): Promise<LoadoutRecord[] | null> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/me/loadouts`);
  } catch {
    return null;
  }
  if (res.status === 503) {
    // Matches api.py's /me/loadouts 503 detail. Any other 503 (say a proxy with no
    // machine up) is a load failure, not a config signal -- otherwise a signed-in
    // player's saves would silently land in localStorage.
    const body = await res.json().catch(() => null);
    if (body?.detail === "loadouts service unavailable") {
      throw new LoadoutsUnavailableError("loadouts not configured");
    }
    return null;
  }
  if (!res.ok) return null;
  try {
    return (await res.json()).loadouts;
  } catch {
    return null;
  }
}

export async function createLoadout(
  name: string,
  cls: string,
  defindexes: number[]
): Promise<LoadoutRecord> {
  const res = await fetch(`${BASE}/me/loadouts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, cls, defindexes }),
  });
  if (!res.ok) throw new Error(`loadouts ${res.status}`);
  return res.json();
}

export async function updateLoadout(
  id: string,
  patch: { name?: string; cls?: string; defindexes?: number[] }
): Promise<LoadoutRecord> {
  const res = await fetch(`${BASE}/me/loadouts/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`loadouts ${res.status}`);
  return res.json();
}

export async function deleteLoadout(id: string): Promise<void> {
  const res = await fetch(`${BASE}/me/loadouts/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`loadouts ${res.status}`);
}

/**
 * Longest message the chat box accepts. The API caps `message` at 2000
 * (`MAX_MESSAGE_CHARS` in `api.py`) and `ChatPanel` prepends `[Current class: ...]\n`
 * (at most 27 characters for a real class name).
 */
export const MAX_MESSAGE_CHARS = 1900;

export async function sendChat(
  message: string,
  history: unknown[],
  equipped: number[],
  llm: LlmKey
): Promise<ChatReply> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: chatHeaders(llm),
    body: JSON.stringify({ message, history, equipped }),
  });
  throwForChatStatus(res);
  return res.json();
}

/** One newline-delimited JSON line from /chat/stream. */
export interface ChatStreamEvent {
  kind: "tool" | "final" | "error";
  name?: string;
  message?: string;
  suggested_defindexes?: number[];
  conflicts?: Conflict[];
  history?: unknown[];
  detail?: string;
  /** Set on an `error` event the player can fix themselves: the provider said 401. */
  code?: "bad_key";
}

/**
 * Stream a chat turn, invoking `onEvent` per line.
 *
 * Once the response starts, failures arrive as an `error` event rather than an HTTP
 * status, since the status is already sent by then.
 */
export async function streamChat(
  message: string,
  history: unknown[],
  equipped: number[],
  llm: LlmKey,
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: chatHeaders(llm),
    body: JSON.stringify({ message, history, equipped }),
  });
  throwForChatStatus(res);
  if (!res.body) throw new Error(`chat ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // A chunk can split a line, so only parse up to the last newline.
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

const CURRENCY_LABEL: Record<string, string> = {
  metal: "ref",
  keys: "keys",
  key: "key",
  hat: "hat",
  usd: "USD",
};

export function formatPrice(p: Price): string {
  const unit = CURRENCY_LABEL[p.currency] ?? p.currency;
  const lo = +p.value.toFixed(2);
  return p.value_high ? `${lo}–${+p.value_high.toFixed(2)} ${unit}` : `${lo} ${unit}`;
}

// backpack.tf overview page — quality-agnostic, keyed on item name.
export function backpackUrl(name: string): string {
  return `https://backpack.tf/overview/${encodeURIComponent(name)}`;
}
