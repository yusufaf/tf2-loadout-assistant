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

// Module-level, not per-hook-instance: guards the copy-to-account flow (the confirm
// dialog + the create loop) against running twice concurrently -- React StrictMode's
// synchronous double-invoke of this effect in dev being the main way that'd happen.
// Doubling the read-only hydration below is harmless; doubling window.confirm() or
// the loadout-creation loop is not.
let copyPromptInFlight = false;

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
      if (
        remoteLoadouts.length === 0 &&
        local.length > 0 &&
        !alreadyPromptedToCopy() &&
        !copyPromptInFlight
      ) {
        copyPromptInFlight = true;
        try {
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
        } finally {
          copyPromptInFlight = false;
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
      // importJson can't be async without changing UseSavedLoadouts for the local
      // path too, so a failure here can't ride back through the caller's own
      // promise chain (App.tsx's onImportFile awaits this call, not the network
      // request it kicks off) -- surface it here directly, or the user sees no
      // error at all and believes the import worked.
      Promise.all(parsed.loadouts.map((l) => remote.create(l.name, l.cls, l.items)))
        .then((created) => setLoadouts((prev) => [...created, ...prev]))
        .catch(() => {
          window.alert(
            "Couldn't import to your account -- check your connection and try again."
          );
        });
    },
  };
}
