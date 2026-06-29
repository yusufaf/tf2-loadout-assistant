import type { Cosmetic } from "./api";

interface SharePayload {
  c: string; // class
  d: number[]; // defindexes
}

// URL-safe base64 (btoa + +/= → -/_ stripped) so the link survives copy/paste.
function toUrlSafe(b64: string): string {
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromUrlSafe(s: string): string {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4 ? "=".repeat(4 - (b64.length % 4)) : "";
  return b64 + pad;
}

// Encode only class + defindexes — keeps URLs short; the recipient fetches fresh
// item data (prices/images) from the API on load.
export function encodeLoadout(cls: string, items: Cosmetic[]): string {
  const payload: SharePayload = { c: cls, d: items.map((i) => i.defindex) };
  return toUrlSafe(btoa(JSON.stringify(payload)));
}

export function decodeLoadout(param: string): { cls: string; defindexes: number[] } | null {
  try {
    const json = atob(fromUrlSafe(param));
    const parsed = JSON.parse(json) as Partial<SharePayload>;
    if (typeof parsed.c !== "string" || !Array.isArray(parsed.d)) return null;
    const defindexes = parsed.d.filter((n) => typeof n === "number");
    return { cls: parsed.c, defindexes };
  } catch {
    return null;
  }
}

export function shareUrl(cls: string, items: Cosmetic[]): string {
  return `${location.origin}${location.pathname}?build=${encodeLoadout(cls, items)}`;
}
