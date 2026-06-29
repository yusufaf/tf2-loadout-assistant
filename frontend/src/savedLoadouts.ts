import type { Cosmetic } from "./api";

export interface SavedLoadout {
  id: string;
  name: string;
  cls: string;
  items: Cosmetic[];
  createdAt: number;
  updatedAt: number;
}

interface StoredFile {
  version: 1;
  loadouts: SavedLoadout[];
}

const STORAGE_KEY = "tf2-saved-loadouts/v1";

function newId(): string {
  // crypto.randomUUID is widely available; fall back just in case.
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isSavedLoadout(v: unknown): v is SavedLoadout {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.id === "string" &&
    typeof o.name === "string" &&
    typeof o.cls === "string" &&
    Array.isArray(o.items)
  );
}

export function loadAll(): SavedLoadout[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Partial<StoredFile>;
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.loadouts)) return [];
    return parsed.loadouts.filter(isSavedLoadout);
  } catch {
    return [];
  }
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
    name: name.trim() || "Untitled loadout",
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
    l.id === id ? { ...l, name: name.trim() || l.name, updatedAt: Date.now() } : l
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

export function importFile(json: string): SavedLoadout[] {
  const parsed = JSON.parse(json) as Partial<StoredFile>;
  if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.loadouts)) {
    throw new Error("Not a TF2 loadouts file.");
  }
  const incoming = parsed.loadouts.filter(isSavedLoadout);
  const existing = loadAll();
  const existingIds = new Set(existing.map((l) => l.id));
  // Merge: re-id any collisions so imports never clobber existing builds.
  const merged = incoming.map((l) =>
    existingIds.has(l.id) ? { ...l, id: newId() } : l
  );
  const next = [...merged, ...existing];
  saveAll(next);
  return next;
}
