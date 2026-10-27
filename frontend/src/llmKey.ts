/**
 * The player's own LLM credential, kept in this browser only.
 *
 * The server never stores it: it rides each chat request as headers (see `api.ts`)
 * and is gone when the request is. Same storage conventions as `savedLoadouts.ts`:
 * a versioned envelope, and every storage call wrapped so private mode or a full
 * quota degrades to "no key" rather than an exception in the panel.
 *
 * By default the key is remembered in `localStorage`. The player can instead keep
 * it for this tab only, in `sessionStorage`: it survives a reload and other tabs
 * don't see it, but a duplicated tab gets a copy and reopening a closed tab or
 * restoring the session brings it back, unless a sign-out happened since
 * (`signOut.ts`). Either way it sits in plaintext browser
 * storage that any script on the page can read; the tab-only option exists so a
 * shared computer doesn't keep it for every later visitor. Which store holds the
 * key is what `remember` means; the envelope is the same in both.
 */

export interface LlmKey {
  provider: string;
  apiKey: string;
}

export interface SavedLlmKey extends LlmKey {
  /** False when the key is kept in this tab's `sessionStorage` only. */
  remember: boolean;
}

interface StoredKey {
  version: 1;
  provider: string;
  apiKey: string;
}

export const STORAGE_KEY = "tf2-llm-key/v1";

export function serializeKey(key: LlmKey): string {
  const file: StoredKey = {
    version: 1,
    provider: key.provider,
    apiKey: key.apiKey.trim(),
  };
  return JSON.stringify(file);
}

export function parseStoredKey(raw: string | null): LlmKey | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredKey> | null;
    if (
      !parsed ||
      parsed.version !== 1 ||
      typeof parsed.provider !== "string" ||
      typeof parsed.apiKey !== "string" ||
      !parsed.provider ||
      !parsed.apiKey.trim()
    ) {
      return null;
    }
    return { provider: parsed.provider, apiKey: parsed.apiKey.trim() };
  } catch {
    return null;
  }
}

// Thunks, not the globals themselves, so a store that isn't defined at all throws
// inside the caller's try rather than at the call site.
const saved = () => localStorage;
const tabOnly = () => sessionStorage;

function read(store: () => Storage): LlmKey | null {
  try {
    return parseStoredKey(store().getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

function write(store: () => Storage, value: string): void {
  try {
    store().setItem(STORAGE_KEY, value);
  } catch {
    // Quota exceeded / private mode — the key still works for this page load.
  }
}

function remove(store: () => Storage): void {
  try {
    store().removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do; if it can't be read it can't be leaked either.
  }
}

export function loadLlmKey(): SavedLlmKey | null {
  const tab = read(tabOnly);
  if (tab) return { ...tab, remember: false };
  const kept = read(saved);
  return kept ? { ...kept, remember: true } : null;
}

export function saveLlmKey(key: LlmKey, remember: boolean): void {
  write(remember ? saved : tabOnly, serializeKey(key));
  // Even if the write failed: the other copy is the key being replaced, and a
  // tab-only choice must not leave one behind in shared storage.
  remove(remember ? tabOnly : saved);
}

export function clearLlmKey(): void {
  remove(saved);
  remove(tabOnly);
}

/** A tab that missed a sign-out: its own copy belongs to the signed-out player, but
 * the remembered one may already be the next person's. */
export function clearTabOnlyLlmKey(): void {
  remove(tabOnly);
}
