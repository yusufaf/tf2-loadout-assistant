import type { ConflictMatrix } from "./conflicts";

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

/** The API is up but has no LLM configured, so the panel should stay hidden. */
export class ChatUnavailableError extends Error {}

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

export async function signOut(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, { method: "POST" });
}

export async function sendChat(
  message: string,
  history: unknown[],
  equipped: number[]
): Promise<ChatReply> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, equipped }),
  });
  if (res.status === 503) throw new ChatUnavailableError("chat not configured");
  if (!res.ok) throw new Error(`chat ${res.status}`);
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
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, equipped }),
  });
  if (res.status === 503) throw new ChatUnavailableError("chat not configured");
  if (!res.ok || !res.body) throw new Error(`chat ${res.status}`);

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
