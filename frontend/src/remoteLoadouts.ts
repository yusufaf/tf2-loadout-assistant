import {
  createLoadout,
  deleteLoadout,
  fetchLoadouts,
  LoadoutWriteUnconfirmedError,
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
  /** Got no complete answer, so may or may not be on the account. */
  unconfirmed: number;
  /** Not sent because an earlier create couldn't be confirmed; definitely not on the account. */
  notTried: number;
}

/** Copy loadouts onto the account one at a time, in order. Keeps going when a create
 * fails, but stops at the first one it couldn't confirm: that usually means the server
 * is stuck, and each further create would wait out the bound and become another write
 * that may land late. Sequential plus content-de-duplicated on purpose: a partial
 * failure leaves exactly the `created` rows visible, and importing the same file again
 * only creates the missing ones -- no duplicates, which `Promise.all` could not promise.
 * An unconfirmed create stays in `seen`, and the caller re-reads the list, so a later
 * re-import de-duplicates against what actually landed. */
export async function importToAccount(
  incoming: SavedLoadout[],
  existing: SavedLoadout[],
  createFn: (l: SavedLoadout) => Promise<SavedLoadout> = (l) => create(l.name, l.cls, l.items)
): Promise<ImportResult> {
  const seen = new Set(existing.map(contentKey));
  const created: SavedLoadout[] = [];
  let duplicates = 0;
  let failed = 0;
  let unconfirmed = 0;
  let notTried = 0;
  let stopped = false;
  for (const l of incoming) {
    const key = contentKey(l);
    if (seen.has(key)) {
      duplicates++;
      continue;
    }
    seen.add(key);
    if (stopped) {
      notTried++;
      continue;
    }
    try {
      created.push(await createFn(l));
    } catch (e) {
      if (e instanceof LoadoutWriteUnconfirmedError) {
        unconfirmed++;
        stopped = true;
      } else {
        failed++;
      }
    }
  }
  return { created, duplicates, failed, unconfirmed, notTried };
}

export type WriteAction = "save" | "rename" | "delete" | "import";

export interface WriteFailure {
  text: string;
  /** The write may have landed: re-read the list rather than trust the view. */
  reread: boolean;
}

// A write the server is still retrying can land minutes after the re-read, so the
// unconfirmed texts never call the re-read list final, and never say it finished: the
// note shows before the read does, and that read can fail too.
const FAILED_TEXT: Record<WriteAction, string> = {
  save: "Couldn't save the loadout to your Steam account. Try again.",
  rename: "Couldn't rename the loadout. Try again.",
  delete: "Couldn't delete the loadout. Try again.",
  import: "Couldn't import to your account. Try again.",
};

const UNCONFIRMED_TEXT: Record<WriteAction, string> = {
  save:
    "Couldn't confirm the loadout was saved to your Steam account; re-reading your list from it. " +
    "It may take a few minutes to appear, so wait a few minutes and reload the page before saving it again.",
  rename: "Couldn't confirm the rename on your Steam account; re-reading your list from it.",
  delete: "Couldn't confirm the delete on your Steam account; re-reading your list from it.",
  // Unreachable today (importToAccount never throws); kept so every action has a text.
  import:
    "Couldn't confirm the import on your Steam account; re-reading your list from it. " +
    "Builds may take a few minutes to appear, so wait a few minutes and reload the page before importing again.",
};

export function describeWriteFailure(e: unknown, action: WriteAction): WriteFailure {
  return e instanceof LoadoutWriteUnconfirmedError
    ? { text: UNCONFIRMED_TEXT[action], reread: true }
    : { text: FAILED_TEXT[action], reread: false };
}

/** What to tell the player after copying local builds onto the account; null when
 * every one landed or was a duplicate. "The rest are still saved in this browser"
 * covers both failed and not-tried builds. */
export function copySummary(result: ImportResult, localCount: number): WriteFailure | null {
  if (result.failed === 0 && result.unconfirmed === 0) return null;
  const copied = `Copied ${result.created.length} of ${localCount} local loadouts to your account`;
  if (result.unconfirmed === 0) {
    return {
      reread: false,
      text:
        `${copied}; the rest are still saved in this browser. To copy them, sign out, Export, ` +
        "sign back in and Import — builds already copied are skipped.",
    };
  }
  return {
    reread: true,
    text:
      `${copied}; ${result.unconfirmed} couldn't be confirmed and may take a few minutes to appear, ` +
      "so your list is being re-read. The rest are still saved in this browser. To copy them, " +
      "wait a few minutes, then sign out, Export, sign back in and Import — builds already copied are skipped.",
  };
}
