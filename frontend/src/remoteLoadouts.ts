import {
  createLoadout,
  deleteLoadout,
  fetchLoadouts,
  lookupCosmetics,
  updateLoadout,
  type Cosmetic,
  type LoadoutRecord,
} from "./api";
import { MAX_EQUIPPED } from "./conflicts";
import { cleanName, contentKey, type SavedLoadout } from "./savedLoadouts";

// The Steam-account-backed counterpart to savedLoadouts.ts's localStorage functions --
// same shapes where the API allows it, so useSavedLoadouts.ts can pick between the two
// with barely any branching. The one structural difference: the server only ever
// stores defindexes, never full Cosmetic objects (CLAUDE.md's "the catalog is the
// single source of truth" story extends here too), so freshly-fetched records are
// hydrated with one batched `lookupCosmetics` call (POST /cosmetics/lookup) before
// they're SavedLoadouts the UI can render. A defindex the catalog no longer resolves is
// counted per loadout as `unavailable` rather than dropped silently. `create`/`rename`
// skip hydration -- the caller already has the Cosmetic[] it just sent.

/** Join records to their cosmetics. Items keep the record's defindex order; the ones
 * the catalog doesn't have are counted in `unavailable` (omitted when zero). */
export function hydrateRecords(
  records: LoadoutRecord[],
  byDefindex: ReadonlyMap<number, Cosmetic>
): SavedLoadout[] {
  return records.map((record) => {
    const items = record.defindexes
      .map((d) => byDefindex.get(d))
      .filter((c): c is Cosmetic => c !== undefined);
    const unavailable = record.defindexes.length - items.length;
    return {
      id: record.id,
      name: record.name,
      cls: record.cls,
      items,
      createdAt: record.created_at,
      updatedAt: record.updated_at,
      ...(unavailable > 0 ? { unavailable } : {}),
    };
  });
}

/** Throws on a failed read (signed out, network, a failed cosmetic lookup) rather than
 * returning an empty list -- CLAUDE.md's "unknown over guessed" rule. Collapsing a
 * transient failure to "owns zero loadouts" would be actively harmful here: the
 * caller offers to copy local loadouts up on first sight of an empty account, and
 * a network blip must never look like that. A deploy without saved loadouts throws
 * `LoadoutsUnavailableError`, which passes through untouched so the caller can keep
 * the localStorage store. */
export async function loadAll(): Promise<SavedLoadout[]> {
  const records = await fetchLoadouts();
  if (!records) throw new Error("couldn't load saved loadouts");
  const { items } = await lookupCosmetics(records.flatMap((r) => r.defindexes));
  return hydrateRecords(records, new Map(items.map((c) => [c.defindex, c])));
}

export async function create(
  name: string,
  cls: string,
  items: Cosmetic[]
): Promise<SavedLoadout> {
  // Clean here too so a client-side value can never 422 on the server's caps.
  const record = await createLoadout(
    cleanName(name),
    cls,
    items.slice(0, MAX_EQUIPPED).map((c) => c.defindex)
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

export interface ImportResult {
  created: SavedLoadout[];
  /** Skipped because the same build is already on the account or earlier in the file. */
  duplicates: number;
  failed: number;
}

/** Copy loadouts onto the account one at a time, in order, and keep going when one
 * fails. Sequential plus content-de-duplicated on purpose: a partial failure leaves
 * exactly the `created` rows visible, and importing the same file again only creates
 * the missing ones -- no duplicates, which `Promise.all` could not promise. */
export async function importToAccount(
  incoming: SavedLoadout[],
  existing: SavedLoadout[],
  createFn: (l: SavedLoadout) => Promise<SavedLoadout> = (l) => create(l.name, l.cls, l.items)
): Promise<ImportResult> {
  const seen = new Set(existing.map(contentKey));
  const created: SavedLoadout[] = [];
  let duplicates = 0;
  let failed = 0;
  for (const l of incoming) {
    const key = contentKey(l);
    if (seen.has(key)) {
      duplicates++;
      continue;
    }
    seen.add(key);
    try {
      created.push(await createFn(l));
    } catch {
      failed++;
    }
  }
  return { created, duplicates, failed };
}
