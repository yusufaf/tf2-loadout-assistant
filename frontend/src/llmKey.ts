/**
 * The player's own LLM credential, kept in this browser only.
 *
 * The server never stores it: it rides each chat request as headers (see `api.ts`)
 * and is gone when the request is. Same storage conventions as `savedLoadouts.ts`:
 * a versioned envelope, and every storage call wrapped so private mode or a full
 * quota degrades to "no key" rather than an exception in the panel.
 */

export interface LlmKey {
  provider: string;
  apiKey: string;
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

export function loadLlmKey(): LlmKey | null {
  try {
    return parseStoredKey(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

export function saveLlmKey(key: LlmKey): void {
  try {
    localStorage.setItem(STORAGE_KEY, serializeKey(key));
  } catch {
    // Quota exceeded / private mode — the key still works for this page load.
  }
}

export function clearLlmKey(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do; if it can't be read it can't be leaked either.
  }
}
