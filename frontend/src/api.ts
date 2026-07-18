export interface Price {
  currency: string;
  value: number;
  value_high: number | null;
}

export interface Cosmetic {
  defindex: number;
  name: string;
  equip_regions: string[];
  used_by_classes: string[];
  item_slot: string | null;
  image_url: string | null;
  price: Price | null;
}

export interface Conflict {
  a: number;
  b: number;
  regions: string[];
}

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function fetchCosmetics(usedBy: string, q: string): Promise<Cosmetic[]> {
  const params = new URLSearchParams({ used_by: usedBy, limit: "120" });
  if (q) params.set("q", q);
  const res = await fetch(`${BASE}/cosmetics?${params}`);
  if (!res.ok) throw new Error(`cosmetics ${res.status}`);
  return (await res.json()).items;
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

export async function sendChat(
  message: string,
  history: unknown[]
): Promise<ChatReply> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });
  if (res.status === 503) throw new ChatUnavailableError("chat not configured");
  if (!res.ok) throw new Error(`chat ${res.status}`);
  return res.json();
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
