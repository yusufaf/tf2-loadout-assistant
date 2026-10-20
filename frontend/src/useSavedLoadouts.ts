import { useEffect, useRef, useState } from "react";
import { LoadoutsUnavailableError, type Cosmetic } from "./api";
import * as store from "./savedLoadouts";
import { cleanName, type ParsedLoadouts, type SavedLoadout } from "./savedLoadouts";
import * as remote from "./remoteLoadouts";

export type SavedStatus = "ready" | "loading" | "error";

export interface SavedMessage {
  kind: "error" | "info";
  text: string;
}

export interface UseSavedLoadouts {
  loadouts: SavedLoadout[];
  /** "loading"/"error" only while signed in: the account list hasn't been read. */
  status: SavedStatus;
  /** A mutation is in flight; the UI disables mutating controls. */
  busy: boolean;
  message: SavedMessage | null;
  dismissMessage: () => void;
  retry: () => void;
  save: (name: string, cls: string, items: Cosmetic[]) => void;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  exportJson: () => string;
  importJson: (json: string) => void;
}

// One-shot flag so the "copy to your account" prompt below only ever fires once per
// Steam account in a browser, not on every sign-in.
function copyPromptKey(steamId: string): string {
  return `tf2-saved-loadouts/copy-prompted/v2/${steamId}`;
}

function alreadyPromptedToCopy(steamId: string): boolean {
  try {
    return localStorage.getItem(copyPromptKey(steamId)) === "1";
  } catch {
    return true; // private mode etc. -- don't nag every mount
  }
}

function markPromptedToCopy(steamId: string): void {
  try {
    localStorage.setItem(copyPromptKey(steamId), "1");
  } catch {
    // ignore -- quota / private mode
  }
}

// Module-level, not per-hook-instance: guards the copy-to-account flow (the confirm
// dialog + the create loop) against running twice concurrently -- React StrictMode's
// synchronous double-invoke of this effect in dev being the main way that'd happen.
// Doubling the read-only hydration below is harmless; doubling window.confirm() or
// the loadout-creation loop is not.
let copyPromptInFlight = false;

interface View {
  /** Where `loadouts` came from. Mutations route on this, never on sign-in state, so a
   * record is only ever sent to the store that issued its id. */
  source: "local" | "remote";
  loadouts: SavedLoadout[];
}

// Picks between the localStorage utility and the Steam-account-backed API by
// sign-in state, behind the identical UseSavedLoadouts interface -- the swap seam
// savedLoadouts.ts documented before Steam sign-in existed. App.tsx doesn't branch
// on `signedIn` itself; it just always gets back whichever store is live.
export function useSavedLoadouts(steamId: string | null): UseSavedLoadouts {
  const [view, setView] = useState<View>(() => ({
    source: "local",
    loadouts: store.loadAll(),
  }));
  const [status, setStatus] = useState<SavedStatus>("ready");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<SavedMessage | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  // Bumped whenever the effect below tears down (sign-in/out, retry, unmount): an async
  // result that started under an older value is stale and must not touch state. The
  // counter sits in an object so the effect's cleanup can hold it directly, not read
  // `ref.current` after the fact.
  const epoch = useRef({ n: 0 });

  // Signing in swaps the source of truth to the account; signing out swaps it back.
  // The one-time copy prompt only fires the direction that matters: local -> account,
  // and only when the account doesn't already have builds of its own.
  useEffect(() => {
    const gen = epoch.current;
    const my = gen.n;
    const showLocal = () => {
      const stored = store.readStored();
      setView({ source: "local", loadouts: stored.loadouts });
      setStatus("ready");
      const skipped = store.skippedText(stored, "saved in this browser");
      setMessage(skipped ? { kind: "info", text: skipped } : null);
    };
    if (steamId === null) {
      showLocal();
      return () => {
        gen.n++;
      };
    }
    // Local records are never displayed under a remote source: until the account list
    // is read the list is empty and the mutating controls are disabled.
    setView({ source: "remote", loadouts: [] });
    setStatus("loading");
    setMessage(null);
    (async () => {
      let remoteLoadouts: SavedLoadout[];
      try {
        remoteLoadouts = await remote.loadAll();
      } catch (e) {
        if (my !== gen.n) return;
        // A deploy without DynamoDB keeps localStorage as the store, same as signed
        // out. Any other failed read (network blip, transient API error) must never
        // look like "this account owns zero loadouts" -- that's exactly the case the
        // copy prompt below guards against firing on -- so it shows an error + Retry.
        if (e instanceof LoadoutsUnavailableError) showLocal();
        else setStatus("error");
        return;
      }
      if (my !== gen.n) return;
      let created: SavedLoadout[] = [];
      let copyFailed: string | null = null;
      const local = store.loadAll();
      if (
        remoteLoadouts.length === 0 &&
        local.length > 0 &&
        !alreadyPromptedToCopy(steamId) &&
        !copyPromptInFlight
      ) {
        copyPromptInFlight = true;
        try {
          const copy = window.confirm(
            `Copy your ${local.length} locally saved loadout${local.length === 1 ? "" : "s"} to your Steam account?`
          );
          markPromptedToCopy(steamId);
          if (copy) {
            // Passing remoteLoadouts (empty here) keeps the dedupe right if the guard
            // above ever loosens.
            const result = await remote.importToAccount(local, remoteLoadouts);
            created = result.created;
            if (result.failed) {
              copyFailed =
                `Copied ${result.created.length} of ${local.length} local loadouts to your account; ` +
                "the rest are still saved in this browser. To copy them, sign out, Export, " +
                "sign back in and Import — builds already copied are skipped.";
            }
          }
        } finally {
          copyPromptInFlight = false;
        }
        if (my !== gen.n) return;
      }
      setView({ source: "remote", loadouts: [...created, ...remoteLoadouts] });
      setStatus("ready");
      if (copyFailed) setMessage({ kind: "error", text: copyFailed });
    })();
    return () => {
      gen.n++;
    };
  }, [steamId, reloadToken]);

  const idle = status === "ready" && !busy;

  // Runs one account mutation. Captures the epoch itself and hands the task an alive()
  // check, so a result that lands after sign-out never writes account data (or account
  // wording) over the local list. busy always clears, or a sign-out mid-request would
  // leave the controls disabled.
  const run = async (task: (alive: () => boolean) => Promise<void>, failText: string) => {
    const gen = epoch.current;
    const my = gen.n;
    const alive = () => my === gen.n;
    setBusy(true);
    setMessage(null);
    try {
      await task(alive);
    } catch {
      if (alive()) setMessage({ kind: "error", text: failText });
    } finally {
      setBusy(false);
    }
  };

  return {
    loadouts: view.loadouts,
    status,
    busy,
    message,
    dismissMessage: () => setMessage(null),
    retry: () => setReloadToken((t) => t + 1),
    save: (name, cls, items) => {
      if (!idle) return;
      if (view.source === "local") {
        setView({ source: "local", loadouts: store.create(name, cls, items) });
        return;
      }
      void run(async (alive) => {
        const l = await remote.create(name, cls, items);
        if (alive()) setView((v) => ({ ...v, loadouts: [l, ...v.loadouts] }));
      }, "Couldn't save the loadout to your Steam account. Try again.");
    },
    rename: (id, name) => {
      if (!idle) return;
      if (view.source === "local") {
        setView({ source: "local", loadouts: store.rename(id, name) });
        return;
      }
      const current = view.loadouts.find((l) => l.id === id);
      if (!current) return;
      void run(async (alive) => {
        const updated = await remote.rename(id, cleanName(name, current.name), current.items);
        const next: SavedLoadout =
          current.unavailable !== undefined
            ? { ...updated, unavailable: current.unavailable }
            : updated;
        if (alive()) {
          setView((v) => ({ ...v, loadouts: v.loadouts.map((l) => (l.id === id ? next : l)) }));
        }
      }, "Couldn't rename the loadout. Try again.");
    },
    remove: (id) => {
      if (!idle) return;
      if (view.source === "local") {
        setView({ source: "local", loadouts: store.remove(id) });
        return;
      }
      void run(async (alive) => {
        await remote.remove(id);
        if (alive()) setView((v) => ({ ...v, loadouts: v.loadouts.filter((l) => l.id !== id) }));
      }, "Couldn't delete the loadout. Try again.");
    },
    exportJson: () =>
      view.source === "remote"
        ? JSON.stringify(
            { version: 1, loadouts: view.loadouts.map(({ unavailable: _, ...l }) => l) },
            null,
            2
          )
        : store.exportFile(),
    importJson: (json) => {
      if (!idle) return;
      let parsed: ParsedLoadouts;
      try {
        parsed = store.parseLoadoutsFile(json);
      } catch {
        setMessage({ kind: "error", text: "Couldn't import — not a valid TF2 loadouts file." });
        return;
      }
      const skipped = store.skippedText(parsed, "in that file");
      if (parsed.loadouts.length === 0) {
        setMessage({
          kind: "error",
          text: skipped ? `No valid loadouts in that file. ${skipped}` : "No valid loadouts in that file.",
        });
        return;
      }
      if (view.source === "local") {
        const r = store.importLoadouts(parsed.loadouts);
        setView({ source: "local", loadouts: r.loadouts });
        setMessage({ kind: "info", text: store.importSummary(r.added, r.duplicates, skipped) });
        return;
      }
      void run(async (alive) => {
        const r = await remote.importToAccount(parsed.loadouts, view.loadouts);
        if (!alive()) return;
        setView((v) => ({ ...v, loadouts: [...r.created, ...v.loadouts] }));
        setMessage({
          kind: r.failed ? "error" : "info",
          text: store.importSummary(r.created.length, r.duplicates, skipped, r.failed),
        });
      }, "Couldn't import to your account. Try again.");
    },
  };
}
