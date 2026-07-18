import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCosmetics,
  fetchCosmetic,
  fetchConflicts,
  fetchChatAvailable,
  formatPrice,
  backpackUrl,
  type Cosmetic,
  type Conflict,
} from "./api";
import { useSavedLoadouts } from "./useSavedLoadouts";
import { decodeLoadout, shareUrl } from "./shareLink";
import ChatPanel from "./ChatPanel";

const CLASSES = [
  "Scout",
  "Soldier",
  "Pyro",
  "Demoman",
  "Heavy",
  "Engineer",
  "Medic",
  "Sniper",
  "Spy",
];

function priceTotals(items: Cosmetic[]): string {
  const sums = new Map<string, number>();
  for (const it of items) {
    if (!it.price) continue;
    sums.set(it.price.currency, (sums.get(it.price.currency) ?? 0) + it.price.value);
  }
  if (sums.size === 0) return "—";
  const label: Record<string, string> = { metal: "ref", keys: "keys", hat: "hat" };
  return [...sums]
    .map(([cur, v]) => `${+v.toFixed(2)} ${label[cur] ?? cur}`)
    .join(" + ");
}

export default function App() {
  const [cls, setCls] = useState("Spy");
  const [query, setQuery] = useState("");
  const [cosmetics, setCosmetics] = useState<Cosmetic[]>([]);
  const [status, setStatus] = useState("");
  const [loadout, setLoadout] = useState<Cosmetic[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [shareNote, setShareNote] = useState("");
  const [chatAvailable, setChatAvailable] = useState(false);
  const saved = useSavedLoadouts();
  const importRef = useRef<HTMLInputElement>(null);

  // Hide the advisor entirely when the API has no LLM configured.
  useEffect(() => {
    fetchChatAvailable().then(setChatAvailable);
  }, []);

  // Load a shared build from ?build=... once on mount, then strip the param.
  useEffect(() => {
    const param = new URLSearchParams(location.search).get("build");
    if (!param) return;
    const decoded = decodeLoadout(param);
    history.replaceState(null, "", location.pathname);
    if (!decoded) return;
    setCls(decoded.cls);
    setStatus("Loading shared loadout…");
    Promise.all(
      decoded.defindexes.map((d) => fetchCosmetic(d).catch(() => null))
    )
      .then((items) => {
        setLoadout(items.filter((i): i is Cosmetic => i !== null));
        setStatus("");
      })
      .catch(() => setStatus(""));
  }, []);

  // Load cosmetics whenever class or (debounced) query changes.
  useEffect(() => {
    let active = true;
    setStatus("Loading the backpack…");
    const t = setTimeout(() => {
      fetchCosmetics(cls, query)
        .then((items) => {
          if (!active) return;
          setCosmetics(items);
          setStatus(items.length ? "" : "No cosmetics match.");
        })
        .catch(() => active && setStatus("Can't reach the bench. Is the API running?"));
    }, 200);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [cls, query]);

  // Re-check conflicts whenever the loadout changes.
  useEffect(() => {
    let active = true;
    fetchConflicts(loadout.map((c) => c.defindex))
      .then((c) => active && setConflicts(c))
      .catch(() => active && setConflicts([]));
    return () => {
      active = false;
    };
  }, [loadout]);

  const clashing = useMemo(() => {
    const s = new Set<number>();
    for (const c of conflicts) {
      s.add(c.a);
      s.add(c.b);
    }
    return s;
  }, [conflicts]);

  const equippedIds = useMemo(
    () => new Set(loadout.map((c) => c.defindex)),
    [loadout]
  );

  function equip(item: Cosmetic) {
    setLoadout((l) =>
      l.some((c) => c.defindex === item.defindex)
        ? l.filter((c) => c.defindex !== item.defindex) // click again to remove
        : [...l, item]
    );
  }
  function unequip(defindex: number) {
    setLoadout((l) => l.filter((c) => c.defindex !== defindex));
  }

  function saveCurrent() {
    const name = window.prompt("Name this loadout:", `${cls} build`);
    if (name === null) return;
    saved.save(name, cls, loadout);
  }

  function loadSaved(items: Cosmetic[], savedCls: string) {
    setCls(savedCls);
    setLoadout(items);
  }

  function renameSaved(id: string, current: string) {
    const name = window.prompt("Rename loadout:", current);
    if (name === null) return;
    saved.rename(id, name);
  }

  function deleteSaved(id: string, name: string) {
    if (window.confirm(`Delete "${name}"?`)) saved.remove(id);
  }

  async function shareSaved(savedCls: string, items: Cosmetic[]) {
    const url = shareUrl(savedCls, items);
    try {
      await navigator.clipboard.writeText(url);
      setShareNote("Link copied!");
    } catch {
      setShareNote(url);
    }
    setTimeout(() => setShareNote(""), 2500);
  }

  function exportLoadouts() {
    const blob = new Blob([saved.exportJson()], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tf2-loadouts.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-importing the same file
    if (!file) return;
    file
      .text()
      .then((text) => saved.importJson(text))
      .catch(() => window.alert("Couldn't import — not a valid TF2 loadouts file."));
  }

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          Mann Co. <span className="co">Loadout</span> Bench
        </h1>
        <span className="tagline">Try it on before you trade for it</span>
      </header>

      <div className="bench">
        <section>
          <div className="classbar">
            {CLASSES.map((c) => (
              <button
                key={c}
                className="class-chip"
                aria-pressed={c === cls}
                onClick={() => setCls(c)}
              >
                <img className="class-icon" src={`/classes/${c.toLowerCase()}.png`} alt="" />
                {c}
              </button>
            ))}
          </div>

          <div className="toolbar">
            <input
              className="search"
              placeholder={`Search ${cls} cosmetics…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {status ? (
            <p className="status">{status}</p>
          ) : (
            <div className="grid">
              {cosmetics.map((c) => (
                <div
                  key={c.defindex}
                  className={equippedIds.has(c.defindex) ? "cell selected" : "cell"}
                  role="button"
                  tabIndex={0}
                  aria-pressed={equippedIds.has(c.defindex)}
                  onClick={() => equip(c)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      equip(c);
                    }
                  }}
                  title={equippedIds.has(c.defindex) ? "Remove from loadout" : "Add to loadout"}
                >
                  <div className="thumb">
                    {equippedIds.has(c.defindex) && <span className="equipped-tick">✓</span>}
                    {c.image_url && <img src={c.image_url} alt="" loading="lazy" />}
                  </div>
                  <div className="name">{c.name}</div>
                  <div className="regions">
                    {c.equip_regions.map((r) => (
                      <span key={r} className="region">
                        {r}
                      </span>
                    ))}
                  </div>
                  <div className="meta">
                    <span className={c.price ? "price" : "price none"}>
                      {c.price ? formatPrice(c.price) : "unpriced"}
                    </span>
                    <a
                      className="bptf"
                      href={backpackUrl(c.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`${c.name} on backpack.tf`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      backpack.tf ↗
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <aside className="tray">
          <h2>Loadout</h2>
          {loadout.length === 0 ? (
            <p className="empty">
              Click a cosmetic to equip it. Conflicts get stamped here.
            </p>
          ) : (
            <>
              <div className="equipped">
                {loadout.map((c) => (
                  <div
                    key={c.defindex}
                    className={clashing.has(c.defindex) ? "slot clash" : "slot"}
                  >
                    {c.image_url && <img src={c.image_url} alt="" />}
                    <span className="slot-name">{c.name}</span>
                    <button
                      className="remove"
                      aria-label={`Remove ${c.name}`}
                      onClick={() => unequip(c.defindex)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>

              {conflicts.length > 0 && (
                <div className="stamp">
                  Incompatible
                  <small>
                    {conflicts.length} clash{conflicts.length > 1 ? "es" : ""} — items
                    share an equip region and can't be worn together.
                  </small>
                </div>
              )}

              <div className="totals">
                <span className="label">Est. value</span>
                <span className="sum">{priceTotals(loadout)}</span>
              </div>

              <a
                className="handoff"
                href="https://loadout.tf/"
                target="_blank"
                rel="noopener noreferrer"
              >
                See it in 3D ↗
              </a>

              <div className="loadout-actions">
                <button className="save-btn" onClick={saveCurrent}>
                  Save loadout
                </button>
                <button
                  className="share-btn"
                  onClick={() => shareSaved(cls, loadout)}
                >
                  Share ↗
                </button>
              </div>
            </>
          )}

          {shareNote && <p className="share-note">{shareNote}</p>}

          <section className="saved">
            <div className="saved-head">
              <h3>Saved loadouts</h3>
              <div className="saved-io">
                <button onClick={exportLoadouts} title="Download all as JSON">
                  Export
                </button>
                <button
                  onClick={() => importRef.current?.click()}
                  title="Import from JSON"
                >
                  Import
                </button>
                <input
                  ref={importRef}
                  type="file"
                  accept="application/json"
                  hidden
                  onChange={onImportFile}
                />
              </div>
            </div>

            {saved.loadouts.length === 0 ? (
              <p className="empty">No saved loadouts yet.</p>
            ) : (
              <ul className="saved-list">
                {saved.loadouts.map((l) => (
                  <li key={l.id} className="saved-item">
                    <div className="saved-meta">
                      <span className="saved-name">{l.name}</span>
                      <small>
                        {l.cls} · {l.items.length} item
                        {l.items.length === 1 ? "" : "s"}
                      </small>
                    </div>
                    <div className="saved-buttons">
                      <button onClick={() => loadSaved(l.items, l.cls)}>Load</button>
                      <button onClick={() => shareSaved(l.cls, l.items)}>Share</button>
                      <button onClick={() => renameSaved(l.id, l.name)}>Rename</button>
                      <button
                        className="danger"
                        onClick={() => deleteSaved(l.id, l.name)}
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {chatAvailable && (
            <ChatPanel cls={cls} loadout={loadout} onEquip={setLoadout} />
          )}
        </aside>
      </div>

      <footer className="site-footer">
        <p>
          Class icons from the{" "}
          <a href="https://wiki.teamfortress.com/" target="_blank" rel="noopener noreferrer">
            Official TF2 Wiki
          </a>
          , licensed{" "}
          <a
            href="https://creativecommons.org/licenses/by-nc-sa/3.0/"
            target="_blank"
            rel="noopener noreferrer"
          >
            CC BY-NC-SA 3.0
          </a>
          . Pricing data from{" "}
          <a href="https://backpack.tf/" target="_blank" rel="noopener noreferrer">
            backpack.tf
          </a>
          .
        </p>
        <p>
          Team Fortress 2 and all related item images are trademarks of Valve Corporation.
          This is an unofficial fan project, not affiliated with or endorsed by Valve.
        </p>
      </footer>
    </div>
  );
}
