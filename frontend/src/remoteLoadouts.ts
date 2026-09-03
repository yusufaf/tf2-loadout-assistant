import {
  createLoadout,
  deleteLoadout,
  fetchCosmetic,
  fetchLoadouts,
  updateLoadout,
  type Cosmetic,
  type LoadoutRecord,
} from "./api";
import type { SavedLoadout } from "./savedLoadouts";

// The Steam-account-backed counterpart to savedLoadouts.ts's localStorage functions --
// same shapes where the API allows it, so useSavedLoadouts.ts can pick between the two
// with barely any branching. The one structural difference: the server only ever
// stores defindexes, never full Cosmetic objects (CLAUDE.md's "the catalog is the
// single source of truth" story extends here too), so a freshly-fetched record has to
// be hydrated against /cosmetics/{defindex} before it's a SavedLoadout the UI can
// render. `create`/`rename` skip that hydration -- the caller already has the
// Cosmetic[] it just sent, no need to fetch it straight back.

async function hydrate(record: LoadoutRecord): Promise<SavedLoadout> {
  const items = (
    await Promise.all(
      record.defindexes.map((d) => fetchCosmetic(d).catch(() => null))
    )
  ).filter((c): c is Cosmetic => c !== null);
  return {
    id: record.id,
    name: record.name,
    cls: record.cls,
    items,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
  };
}

/** Throws on a failed read (signed out, unconfigured, network) rather than
 * returning an empty list -- CLAUDE.md's "unknown over guessed" rule. Collapsing a
 * transient failure to "owns zero loadouts" would be actively harmful here: the
 * caller offers to copy local loadouts up on first sight of an empty account, and
 * a network blip must never look like that. */
export async function loadAll(): Promise<SavedLoadout[]> {
  const records = await fetchLoadouts();
  if (!records) throw new Error("couldn't load saved loadouts");
  return Promise.all(records.map(hydrate));
}

export async function create(
  name: string,
  cls: string,
  items: Cosmetic[]
): Promise<SavedLoadout> {
  const record = await createLoadout(
    name,
    cls,
    items.map((c) => c.defindex)
  );
  return {
    id: record.id,
    name: record.name,
    cls: record.cls,
    items,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
  };
}

export async function rename(
  id: string,
  name: string,
  items: Cosmetic[]
): Promise<SavedLoadout> {
  const record = await updateLoadout(id, { name });
  return {
    id: record.id,
    name: record.name,
    cls: record.cls,
    items,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
  };
}

export async function remove(id: string): Promise<void> {
  await deleteLoadout(id);
}
