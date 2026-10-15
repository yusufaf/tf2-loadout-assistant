import type { Cosmetic } from "./api";
import { CLASSES } from "./classes";
import { MAX_EQUIPPED } from "./conflicts";

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
    // The class rides into every chat prompt, and the list drives one fetch per item:
    // a crafted link must not be able to set either to something unbounded.
    if (!CLASSES.includes(parsed.c)) return null;
    const defindexes = [...new Set(parsed.d.filter((n) => typeof n === "number"))].slice(
      0,
      MAX_EQUIPPED
    );
    return { cls: parsed.c, defindexes };
  } catch {
    return null;
  }
}

export function shareUrl(cls: string, items: Cosmetic[]): string {
  return `${location.origin}${location.pathname}?build=${encodeLoadout(cls, items)}`;
}
