import type { Cosmetic, Price } from "./api";
import { CLASSES } from "./classes";
import { MAX_EQUIPPED } from "./conflicts";

export interface SavedLoadout {
  id: string;
  name: string;
  cls: string;
  items: Cosmetic[];
  createdAt: number;
  updatedAt: number;
  /** Defindexes saved on the account that the catalog no longer resolves. Set only by
   * the remote store; never written to localStorage or an export. */
  unavailable?: number;
}

interface StoredFile {
  version: 1;
  loadouts: SavedLoadout[];
}

const STORAGE_KEY = "tf2-saved-loadouts/v1";

/** Mirrors `MAX_LOADOUT_NAME_CHARS` in api.py: a longer name 422s on the account path. */
export const MAX_LOADOUT_NAME_CHARS = 100;

function newId(): string {
  // crypto.randomUUID is widely available; fall back just in case.
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Trim, fall back when blank, and cut to the server's name cap. Truncates by code
 * point (what pydantic's `max_length` counts), so an emoji at the boundary is never
 * split into a lone surrogate. The fallback is cut too: account rows saved before the
 * server capped names can be longer than the cap. */
export function cleanName(name: string, fallback = "Untitled loadout"): string {
  const s = name.trim() || fallback.trim();
  return Array.from(s).slice(0, MAX_LOADOUT_NAME_CHARS).join("");
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isStringArray(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((s) => typeof s === "string");
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function parsePrice(v: unknown): Price | null {
  if (!isObject(v)) return null;
  const { currency, value, value_high, ref_value } = v;
  if (typeof currency !== "string" || !isFiniteNumber(value)) return null;
  if (value_high !== null && !isFiniteNumber(value_high)) return null;
  if (ref_value !== null && !isFiniteNumber(ref_value)) return null;
  return { currency, value, value_high, ref_value };
}

/** Shape-check one cosmetic from an untrusted source (an import, or localStorage).
 * `defindex`, `name` and `equip_regions` are required -- the clash filter iterates
 * `equip_regions` and would throw without it. Fields older saves predate (paintable,
 * styles) default instead of rejecting, so a legacy build still loads. */
export function parseCosmetic(v: unknown): Cosmetic | null {
  if (!isObject(v)) return null;
  if (!Number.isSafeInteger(v.defindex) || (v.defindex as number) <= 0) return null;
  if (typeof v.name !== "string") return null;
  if (!isStringArray(v.equip_regions)) return null;
  return {
    defindex: v.defindex as number,
    name: v.name,
    equip_regions: v.equip_regions,
    used_by_classes: isStringArray(v.used_by_classes) ? v.used_by_classes : [],
    item_slot: typeof v.item_slot === "string" ? v.item_slot : null,
    image_url: typeof v.image_url === "string" ? v.image_url : null,
    price: parsePrice(v.price),
    paintable: typeof v.paintable === "boolean" ? v.paintable : false,
    holiday_restriction: typeof v.holiday_restriction === "string" ? v.holiday_restriction : null,
    styles: isStringArray(v.styles) ? v.styles : [],
  };
}

/** Normalises one loadout so a valid import can't trip the account API's caps
 * (16 items, 100-char name, one of the nine classes). */
export function parseSavedLoadout(v: unknown, now = Date.now()): SavedLoadout | null {
  if (!isObject(v)) return null;
  if (typeof v.id !== "string" || v.id === "") return null;
  if (typeof v.name !== "string") return null;
  if (typeof v.cls !== "string" || !CLASSES.includes(v.cls)) return null;
  if (!Array.isArray(v.items)) return null;
  const seen = new Set<number>();
  const items: Cosmetic[] = [];
  for (const raw of v.items) {
    const c = parseCosmetic(raw);
    if (!c || seen.has(c.defindex)) continue;
    seen.add(c.defindex);
    items.push(c);
  }
  return {
    id: v.id,
    name: cleanName(v.name),
    cls: v.cls,
    items: items.slice(0, MAX_EQUIPPED),
    createdAt: isFiniteNumber(v.createdAt) ? v.createdAt : now,
    updatedAt: isFiniteNumber(v.updatedAt) ? v.updatedAt : now,
  };
}

export interface ParsedLoadouts {
  loadouts: SavedLoadout[];
  /** Entries rejected outright. */
  invalid: number;
  /** Items dropped from entries that were kept (malformed, duplicate, or over the cap). */
  droppedItems: number;
}

/** The one place that counts what a read repaired, shared by imports and stored data
 * so neither drops anything silently. A repeated id gets a fresh one (not counted --
 * nothing is lost), because `remove`/`rename` match by id and React keys on it. */
export function parseLoadoutList(raw: unknown[], now = Date.now()): ParsedLoadouts {
  const loadouts: SavedLoadout[] = [];
  const ids = new Set<string>();
  let invalid = 0;
  let droppedItems = 0;
  for (const entry of raw) {
    const parsed = parseSavedLoadout(entry, now);
    if (!parsed) {
      invalid++;
      continue;
    }
    droppedItems += (entry as { items: unknown[] }).items.length - parsed.items.length;
    const id = ids.has(parsed.id) ? newId() : parsed.id;
    ids.add(id);
    loadouts.push(id === parsed.id ? parsed : { ...parsed, id });
  }
  return { loadouts, invalid, droppedItems };
}

export function parseLoadoutsFile(json: string, now = Date.now()): ParsedLoadouts {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    throw new Error("Not a TF2 loadouts file.");
  }
  if (!isObject(parsed) || parsed.version !== 1 || !Array.isArray(parsed.loadouts)) {
    throw new Error("Not a TF2 loadouts file.");
  }
  return parseLoadoutList(parsed.loadouts, now);
}

export function readStored(): ParsedLoadouts {
  const empty: ParsedLoadouts = { loadouts: [], invalid: 0, droppedItems: 0 };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return empty;
    const parsed: unknown = JSON.parse(raw);
    if (!isObject(parsed) || parsed.version !== 1 || !Array.isArray(parsed.loadouts)) {
      return empty;
    }
    return parseLoadoutList(parsed.loadouts);
  } catch {
    return empty;
  }
}

export function loadAll(): SavedLoadout[] {
  return readStored().loadouts;
}

/** Plain-language note about what a read skipped, or null when it skipped nothing.
 * `where` completes the sentence ("in that file", "saved in this browser"). */
export function skippedText(
  p: Pick<ParsedLoadouts, "invalid" | "droppedItems">,
  where: string
): string | null {
  const parts: string[] = [];
  if (p.invalid) parts.push(`${p.invalid} unreadable loadout${p.invalid === 1 ? "" : "s"}`);
  if (p.droppedItems) {
    parts.push(`${p.droppedItems} unreadable item${p.droppedItems === 1 ? "" : "s"}`);
  }
  return parts.length ? `Skipped ${parts.join(" and ")} ${where}.` : null;
}

/** Shared by the local and account import paths. On a partial failure it keeps the
 * duplicate and skipped counts, so the message never hides what happened to the rest. */
export function importSummary(
  added: number,
  duplicates: number,
  skipped: string | null,
  failed = 0
): string {
  let text: string;
  if (failed === 0) {
    text = `Imported ${added} loadout${added === 1 ? "" : "s"}`;
    if (duplicates) text += `; ${duplicates} already saved`;
    text += ".";
  } else {
    text =
      `Imported ${added} of ${added + failed}; ${failed} couldn't be saved — ` +
      "import the same file again to retry, builds already saved are skipped.";
    if (duplicates) text += ` ${duplicates} were already saved.`;
  }
  return skipped ? `${text} ${skipped}` : text;
}

/** Identity of a build for import de-duplication: class, cleaned name, and the set of
 * defindexes. The name goes through `cleanName` because account rows saved before the
 * client cleaned names (untrimmed, blank, over the cap) must match their cleaned copy. */
export function contentKey(l: Pick<SavedLoadout, "name" | "cls" | "items">): string {
  const ids = l.items.map((c) => c.defindex).sort((a, b) => a - b);
  return `${l.cls}|${cleanName(l.name)}|${ids.join(",")}`;
}

/** Swap each stored item for the catalog's current copy (prices, images), keeping the
 * stored order. Items the catalog no longer has are dropped and counted. */
export function mergeFresh(
  stored: Cosmetic[],
  fresh: Cosmetic[]
): { items: Cosmetic[]; dropped: number } {
  const byDefindex = new Map(fresh.map((c) => [c.defindex, c]));
  const items: Cosmetic[] = [];
  for (const c of stored) {
    const current = byDefindex.get(c.defindex);
    if (current) items.push(current);
  }
  return { items, dropped: stored.length - items.length };
}

export function saveAll(loadouts: SavedLoadout[]): void {
  try {
    const file: StoredFile = { version: 1, loadouts };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(file));
  } catch {
    // Quota exceeded / private mode — fail quietly; UI state still reflects intent.
  }
}

export function create(name: string, cls: string, items: Cosmetic[]): SavedLoadout[] {
  const now = Date.now();
  const record: SavedLoadout = {
    id: newId(),
    name: cleanName(name),
    cls,
    items,
    createdAt: now,
    updatedAt: now,
  };
  const next = [record, ...loadAll()];
  saveAll(next);
  return next;
}

export function rename(id: string, name: string): SavedLoadout[] {
  const next = loadAll().map((l) =>
    l.id === id ? { ...l, name: cleanName(name, l.name), updatedAt: Date.now() } : l
  );
  saveAll(next);
  return next;
}

export function update(id: string, cls: string, items: Cosmetic[]): SavedLoadout[] {
  const next = loadAll().map((l) =>
    l.id === id ? { ...l, cls, items, updatedAt: Date.now() } : l
  );
  saveAll(next);
  return next;
}

export function remove(id: string): SavedLoadout[] {
  const next = loadAll().filter((l) => l.id !== id);
  saveAll(next);
  return next;
}

export function exportFile(): string {
  const file: StoredFile = { version: 1, loadouts: loadAll() };
  return JSON.stringify(file, null, 2);
}

/** Merge already-validated loadouts (see `parseLoadoutsFile`) into storage. Skips a
 * build whose content matches one already saved or earlier in `incoming`, and re-ids
 * any id collision so imports never clobber existing builds. */
export function importLoadouts(incoming: SavedLoadout[]): {
  loadouts: SavedLoadout[];
  added: number;
  duplicates: number;
} {
  const existing = loadAll();
  const keys = new Set(existing.map(contentKey));
  const ids = new Set(existing.map((l) => l.id));
  const accepted: SavedLoadout[] = [];
  let duplicates = 0;
  for (const l of incoming) {
    const key = contentKey(l);
    if (keys.has(key)) {
      duplicates++;
      continue;
    }
    keys.add(key);
    const id = ids.has(l.id) ? newId() : l.id;
    ids.add(id);
    accepted.push(id === l.id ? l : { ...l, id });
  }
  const next = [...accepted, ...existing];
  saveAll(next);
  return { loadouts: next, added: accepted.length, duplicates };
}
