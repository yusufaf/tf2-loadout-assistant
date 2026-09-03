import { useEffect, useState } from "react";
import type { Cosmetic } from "./api";
import * as store from "./savedLoadouts";
import type { SavedLoadout } from "./savedLoadouts";
import * as remote from "./remoteLoadouts";

export interface UseSavedLoadouts {
  loadouts: SavedLoadout[];
  save: (name: string, cls: string, items: Cosmetic[]) => void;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  exportJson: () => string;
  importJson: (json: string) => void;
}

// One-shot flag so the "copy to your account" prompt below only ever fires once per
// browser, not on every sign-in.
const COPY_PROMPTED_KEY = "tf2-saved-loadouts/copy-prompted/v1";

function alreadyPromptedToCopy(): boolean {
  try {
    return localStorage.getItem(COPY_PROMPTED_KEY) === "1";
  } catch {
    return true; // private mode etc. -- don't nag every mount
  }
}

function markPromptedToCopy(): void {
  try {
    localStorage.setItem(COPY_PROMPTED_KEY, "1");
  } catch {
    // ignore -- quota / private mode
  }
}

// Picks between the localStorage utility and the Steam-account-backed API by
// sign-in state, behind the identical UseSavedLoadouts interface -- the swap seam
// savedLoadouts.ts documented before Steam sign-in existed. App.tsx doesn't branch
// on `signedIn` itself; it just always gets back whichever store is live.
export function useSavedLoadouts(signedIn: boolean): UseSavedLoadouts {
  const [loadouts, setLoadouts] = useState<SavedLoadout[]>(() => store.loadAll());

  // Signing in swaps the source of truth to the account; signing out swaps it back.
  // The one-time copy prompt only fires the direction that matters: local -> account,
  // and only when the account doesn't already have builds of its own.
  useEffect(() => {
    if (!signedIn) {
      setLoadouts(store.loadAll());
      return;
    }
    let active = true;
    (async () => {
      let remoteLoadouts: SavedLoadout[];
      try {
        remoteLoadouts = await remote.loadAll();
      } catch {
        // A failed read (network blip, transient API error) must never look like
        // "this account owns zero loadouts" -- that's exactly the case the copy
        // prompt below guards against firing on. Leave whatever's already showing
        // in place rather than clearing it; the next sign-in-state change retries.
        return;
      }
      const local = store.loadAll();
      if (remoteLoadouts.length === 0 && local.length > 0 && !alreadyPromptedToCopy()) {
        const copy = window.confirm(
          `Copy your ${local.length} locally saved loadout${local.length === 1 ? "" : "s"} to your Steam account?`
        );
        markPromptedToCopy();
        if (copy) {
          for (const l of local) {
            try {
              await remote.create(l.name, l.cls, l.items);
            } catch {
              // best-effort -- a failed copy just leaves that one build local-only
            }
          }
          try {
            remoteLoadouts = await remote.loadAll();
          } catch {
            // The copy itself may still have landed; just show what we had.
          }
        }
      }
      if (active) setLoadouts(remoteLoadouts);
    })();
    return () => {
      active = false;
    };
  }, [signedIn]);

  return {
    loadouts,
    save: (name, cls, items) => {
      if (signedIn) {
        remote.create(name, cls, items).then((l) => setLoadouts((prev) => [l, ...prev]));
      } else {
        setLoadouts(store.create(name, cls, items));
      }
    },
    rename: (id, name) => {
      if (signedIn) {
        const current = loadouts.find((l) => l.id === id);
        if (!current) return;
        remote
          .rename(id, name, current.items)
          .then((updated) => setLoadouts((prev) => prev.map((l) => (l.id === id ? updated : l))));
      } else {
        setLoadouts(store.rename(id, name));
      }
    },
    remove: (id) => {
      if (signedIn) {
        remote.remove(id).then(() => setLoadouts((prev) => prev.filter((l) => l.id !== id)));
      } else {
        setLoadouts(store.remove(id));
      }
    },
    exportJson: () =>
      signedIn ? JSON.stringify({ version: 1, loadouts }, null, 2) : store.exportFile(),
    importJson: (json) => {
      if (!signedIn) {
        setLoadouts(store.importFile(json));
        return;
      }
      const parsed = JSON.parse(json) as { version?: number; loadouts?: SavedLoadout[] };
      if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.loadouts)) {
        throw new Error("Not a TF2 loadouts file.");
      }
      Promise.all(parsed.loadouts.map((l) => remote.create(l.name, l.cls, l.items)))
        .then((created) => setLoadouts((prev) => [...created, ...prev]))
        .catch(() => {
          // A partial-network-failure mid-import leaves whatever did land on the
          // account; the validation throw above is the only case the caller's own
          // catch (the import-file handler's alert) needs to see.
        });
    },
  };
}
