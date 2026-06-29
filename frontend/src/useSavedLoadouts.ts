import { useState } from "react";
import type { Cosmetic } from "./api";
import * as store from "./savedLoadouts";
import type { SavedLoadout } from "./savedLoadouts";

export interface UseSavedLoadouts {
  loadouts: SavedLoadout[];
  save: (name: string, cls: string, items: Cosmetic[]) => void;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  exportJson: () => string;
  importJson: (json: string) => void;
}

// Thin React state wrapper over the localStorage utility. Structured so a future
// backend repository could swap in behind the same interface without UI changes.
export function useSavedLoadouts(): UseSavedLoadouts {
  const [loadouts, setLoadouts] = useState<SavedLoadout[]>(() => store.loadAll());

  return {
    loadouts,
    save: (name, cls, items) => setLoadouts(store.create(name, cls, items)),
    rename: (id, name) => setLoadouts(store.rename(id, name)),
    remove: (id) => setLoadouts(store.remove(id)),
    exportJson: () => store.exportFile(),
    importJson: (json) => setLoadouts(store.importFile(json)),
  };
}
