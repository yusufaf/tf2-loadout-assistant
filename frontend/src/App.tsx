import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  fetchCosmetics,
  fetchConflicts,
  fetchConflictMatrix,
  fetchChatAvailable,
  fetchMe,
  steamLoginUrl,
  fetchInventory,
  lookupCosmetics,
  formatPrice,
  backpackUrl,
  type Cosmetic,
  type Conflict,
} from "./api";
import {
  backpackBusy,
  backpackFailed,
  backpackRefreshFailed,
  ownedDefindexes,
  ownedDisabledReason,
  ownedEnabled,
  reloadRequest,
  settleBackpack,
  type BackpackState,
} from "./backpack";
import {
  DEFAULT_FILTERS,
  applyFilters,
  benchForSuggestion,
  canUndoAdvisor,
  clashingIds,
  rebaseAdvisorUndo,
  toggleEquip,
  type AdvisorUndo,
  type FilterState,
} from "./filters";
import { MAX_EQUIPPED, type ConflictMatrix } from "./conflicts";
import { CLASSES } from "./classes";
import FilterBar from "./FilterBar";
import { useSavedLoadouts } from "./useSavedLoadouts";
import { mergeFresh } from "./savedLoadouts";
import { decodeLoadout, shareUrl } from "./shareLink";
import { withoutParam } from "./urlParams";
import ChatPanel from "./ChatPanel";
import { listenForSignOut, signOutAndForget } from "./signOut";
import {
  recheckRequest,
  settleSignIn,
  signedInAs,
  signedInMe,
  signInFailed,
  signInRechecking,
  signInSettled,
  type SignInState,
} from "./signInCheck";

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

// The tray's initial value. Every edit makes a new array, so identity with this means
// untouched, even when an edit has emptied the tray again.
const NO_LOADOUT: Cosmetic[] = [];

const BENCH_UNREACHABLE = "Can't reach the bench. Is the API running?";

export default function App() {
  const [cls, setCls] = useState("Spy");
  const [query, setQuery] = useState("");
  const [cosmetics, setCosmetics] = useState<Cosmetic[]>([]);
  const [status, setStatus] = useState("Loading the backpack…");
  const [loadout, setLoadout] = useState<Cosmetic[]>(NO_LOADOUT);
  // Layout, not passive: the ref updates in the same commit as an edit, so a lookup that
  // resolves just after a click already sees it.
  const loadoutRef = useRef(loadout);
  useLayoutEffect(() => {
    loadoutRef.current = loadout;
  }, [loadout]);
  const [sharedLoading, setSharedLoading] = useState(false);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [matrix, setMatrix] = useState<ConflictMatrix>({});
  const [shareNote, setShareNote] = useState("");
  // The bench before the last advisor Equip. Offered only while `applied` is still the
  // bench: any later edit makes a new array, or a new class, and retires the Undo. A
  // class switch must also clear it (see `changeClass`), or switching back revives it.
  const [advisorUndo, setAdvisorUndo] = useState<AdvisorUndo | null>(null);
  const [pinnedNotes, setPinnedNotes] = useState<string[]>([]);
  const [chatAvailable, setChatAvailable] = useState(false);
  // The sign-in probe's answer. The advisor waits for the first one to settle
  // (`AUTH_ME_TIMEOUT_MS` bounds it), because a saved conversation is only shown to the
  // account it was made under.
  const [signIn, setSignIn] = useState<SignInState>({ kind: "checking" });
  // Bumped on sign-out to remount ChatPanel empty. Not keyed on the SteamID: a sign-in
  // Retry that turns a failed probe into signed-in would remount it mid-turn. An empty
  // panel switches to the new viewer by itself.
  const [chatEpoch, setChatEpoch] = useState(0);
  // A logout request is in flight: Sign out is disabled and the advisor unmounted, so
  // nothing in this tab can save a key or turn between the clear and the answer.
  const [signingOut, setSigningOut] = useState(false);
  const [authNote, setAuthNote] = useState("");
  // Bumped by the bench Retry to re-run the grid fetch and the mount probes (chat, sign-in,
  // conflict matrix). The backpack has its own Retry.
  const [apiRetry, setApiRetry] = useState(0);
  // Bumped on sign-out so a sign-in probe that was already in flight can't land after it.
  const meToken = useRef(0);
  const [backpack, setBackpack] = useState<BackpackState>({ kind: "signed_out" });
  const backpackToken = useRef(0);
  const me = signedInMe(signIn);
  const steamId = signedInAs(signIn);
  const saved = useSavedLoadouts(steamId);
  const savedLocked = saved.busy || saved.status !== "ready";
  const importRef = useRef<HTMLInputElement>(null);
  const loadToken = useRef(0);

  // Hide the advisor entirely when the API has no LLM configured. Probed at mount and
  // again on the bench Retry; it only ever turns the advisor on, so a Retry while the
  // API is still down can't unmount the panel and its transcript.
  useEffect(() => {
    fetchChatAvailable().then((ok) => setChatAvailable((prev) => prev || ok));
  }, [apiRetry]);

  // Probe sign-in state. A probe in flight at sign-out is dropped by the token.
  function probeSignIn() {
    const token = meToken.current;
    fetchMe().then((m) => {
      if (token === meToken.current) setSignIn((prev) => settleSignIn(prev, m));
    });
  }

  // At mount and on the bench Retry; an unconfigured auth service reads as signed out.
  // The settle rules are in `signInCheck.ts`.
  useEffect(() => probeSignIn(), [apiRetry]);

  // The sign-in note's Retry: re-runs only the probe.
  function recheckSignIn() {
    const next = recheckRequest(signIn);
    if (!next) return;
    setSignIn(next);
    probeSignIn();
  }

  // The session cookie is gone, from this tab's Sign out or another's. `meToken` too: a
  // probe still in flight is dropped.
  function showSignedOut() {
    meToken.current++;
    setSignIn({ kind: "signed_out" });
    setAuthNote("");
  }

  // Another tab started or finished signing out; `listenForSignOut` has already
  // forgotten this tab's key and chat. Remount the advisor so its state goes too, say
  // why it emptied (that tab's request may still fail, leaving this one signed in), and
  // once the shared cookie is cleared, show it.
  useEffect(
    () =>
      listenForSignOut((news) => {
        setChatEpoch((n) => n + 1);
        if (news === "signed_out") return showSignedOut();
        setAuthNote(
          "Signing out in another tab cleared your advisor key and chat from this tab."
        );
      }),
    []
  );

  // Fetch the backpack once signed in. Not re-run on every render -- the API caches
  // it server-side for ~10min, and `reloadBackpack` is the way to re-read it. The token
  // drops a read that lands after sign-out or a newer reload.
  const signedIn = me !== null;
  useEffect(() => {
    const token = ++backpackToken.current;
    if (!signedIn) {
      setBackpack({ kind: "signed_out" });
      return;
    }
    setBackpack({ kind: "loading" });
    fetchInventory().then((r) => {
      if (token === backpackToken.current) setBackpack(settleBackpack({ kind: "loading" }, r));
    });
  }, [signedIn]);

  // Read ?auth=failed left by a rejected /auth/steam/return redirect, then strip it.
  // Each URL effect strips only its own param, so their order doesn't matter.
  useEffect(() => {
    if (new URLSearchParams(location.search).get("auth") !== "failed") return;
    setAuthNote("Steam sign-in failed. Try again.");
    history.replaceState(null, "", withoutParam(location, "auth"));
  }, []);

  // The conflict matrix is static, so fetch it at mount and on the bench Retry, and
  // filter locally afterwards. An empty matrix still detects same-region clashes, so a
  // failure degrades rather than breaks; a failed re-fetch keeps the matrix we have.
  useEffect(() => {
    fetchConflictMatrix()
      .then(setMatrix)
      .catch(() => {});
  }, [apiRetry]);

  // Load a shared build from ?build=... once on mount, then strip the param. It tracks
  // its own loading flag rather than `status`, which belongs to the cosmetics grid --
  // sharing it let this effect blank the grid's loading message mid-fetch.
  useEffect(() => {
    const param = new URLSearchParams(location.search).get("build");
    if (!param) return;
    history.replaceState(null, "", withoutParam(location, "build"));
    const decoded = decodeLoadout(param);
    if (!decoded) {
      pinNote("That share link is broken or out of date.");
      return;
    }
    setCls(decoded.cls);
    setSharedLoading(true);
    lookupCosmetics(decoded.defindexes)
      .then(({ items, missing }) => {
        // The player equipped, unequipped or loaded something while this was in flight, so
        // their edit wins and the link's own notes no longer apply.
        if (loadoutRef.current !== NO_LOADOUT) {
          pinNote("Kept your changes. The shared loadout wasn't applied.");
          return;
        }
        setLoadout(items);
        if (missing.length) {
          pinNote(
            `${missing.length} item${missing.length === 1 ? "" : "s"} from this link couldn't be found in the catalog.`
          );
        }
      })
      .catch(() => pinNote("Couldn't load the shared loadout. Is the API running?"))
      .finally(() => setSharedLoading(false));
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
        .catch(() => active && setStatus(BENCH_UNREACHABLE));
    }, 200);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [cls, query, apiRetry]);

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

  const clashing = useMemo(() => clashingIds(conflicts), [conflicts]);

  const equippedIds = useMemo(
    () => new Set(loadout.map((c) => c.defindex)),
    [loadout]
  );

  const owned = useMemo(() => new Set(ownedDefindexes(backpack)), [backpack]);
  const canFilterOwned = ownedEnabled(backpack);

  // If the toggle was on and the backpack becomes unavailable (sign-out, a Steam
  // privacy change), turn it back off rather than leave the grid silently empty.
  useEffect(() => {
    if (!canFilterOwned) setFilters((f) => (f.ownedOnly ? { ...f, ownedOnly: false } : f));
  }, [canFilterOwned]);

  function reloadBackpack() {
    const req = reloadRequest(backpack);
    if (!req) return;
    const token = ++backpackToken.current;
    setBackpack(req.next);
    fetchInventory(req.refresh).then((r) => {
      if (token === backpackToken.current) setBackpack(settleBackpack(req.next, r));
    });
  }

  const entries = useMemo(
    () => applyFilters(cosmetics, filters, loadout, matrix, owned),
    [cosmetics, filters, loadout, matrix, owned]
  );

  const noteTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(noteTimer.current), []);
  // Passing notes share one slot and clear themselves. Pinned notes (a URL to copy by
  // hand, or a note that fires on page load) each stay until dismissed, so a later
  // passing note never covers one.
  function flashNote(text: string) {
    setShareNote(text);
    // Restart the clock so an earlier note's timer can't blank a newer one early.
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setShareNote(""), 2500);
  }
  function pinNote(text: string) {
    setPinnedNotes((ns) => (ns.includes(text) ? ns : [...ns, text]));
  }
  function unpinNote(text: string) {
    setPinnedNotes((ns) => ns.filter((n) => n !== text));
  }

  function equip(item: Cosmetic) {
    // Read from the render closure rather than a functional updater: the "bench is
    // full" note is a side effect, and StrictMode double-invokes updaters.
    const next = toggleEquip(loadout, item); // click again to remove
    if (next === null) {
      flashNote(`The bench holds ${MAX_EQUIPPED} cosmetics at most.`);
      return;
    }
    setLoadout(next);
  }
  function unequip(defindex: number) {
    setLoadout((l) => l.filter((c) => c.defindex !== defindex));
  }

  function equipSuggestion(items: Cosmetic[], forCls: string | undefined) {
    const token = ++loadToken.current;
    const prev = { cls, loadout };
    const next = benchForSuggestion(prev, items, forCls);
    setCls(next.cls);
    setLoadout(next.loadout);
    setAdvisorUndo({ applied: next, prev });
    // The suggestion may come from a conversation saved days ago.
    void refreshShown(next.loadout, token, "Couldn't refresh prices — showing the advisor's copy.");
  }
  function undoAdvisorEquip() {
    if (!advisorUndo) return;
    const { prev } = advisorUndo;
    const token = ++loadToken.current;
    setCls(prev.cls);
    setLoadout(prev.loadout);
    setAdvisorUndo(null);
    // `prev` can be a Load whose own refresh was cut short by the Equip. Never refresh an
    // empty tray: that would replace NO_LOADOUT, which the shared-link loader keys on.
    if (prev.loadout.length > 0) {
      void refreshShown(prev.loadout, token, "Couldn't refresh prices — showing the earlier copy.");
    }
  }
  function changeClass(c: string) {
    if (c === cls) return;
    setCls(c);
    // Switching back later must not revive an Undo that would also revert the class.
    setAdvisorUndo(null);
  }
  const undoOffered = canUndoAdvisor(advisorUndo, { cls, loadout });

  function saveCurrent() {
    const name = window.prompt("Name this loadout:", `${cls} build`);
    if (name === null) return;
    saved.save(name, cls, loadout);
  }

  async function loadSaved(items: Cosmetic[], savedCls: string) {
    const token = ++loadToken.current; // guards the notes below against a newer Load
    // A saved class can be anything (an imported file, or a row the API saved before it
    // validated classes); an unknown one would leak into the chat prefix, so keep the
    // current class.
    if (CLASSES.includes(savedCls)) setCls(savedCls);
    // Show the saved copy at once, then swap in the catalog's current prices and images.
    const shown = items.slice(0, MAX_EQUIPPED);
    setLoadout(shown);
    await refreshShown(shown, token, "Couldn't refresh prices — showing the saved copy.");
  }

  /** Swap a just-shown stored copy for the catalog's current prices and images. */
  async function refreshShown(shown: Cosmetic[], token: number, failNote: string) {
    try {
      const { items: fresh } = await lookupCosmetics(shown.map((c) => c.defindex));
      const { items: merged, dropped } = mergeFresh(shown, fresh);
      // Identity check: an equip/unequip, advisor equip or another Load since replaced
      // the array, and the player's edit wins over the refresh.
      setLoadout((cur) => (cur === shown ? merged : cur));
      setAdvisorUndo((u) => rebaseAdvisorUndo(u, shown, merged));
      if (token === loadToken.current && dropped) {
        flashNote(
          `${dropped} item${dropped === 1 ? "" : "s"} in this loadout ${dropped === 1 ? "is" : "are"} no longer in the catalog.`
        );
      }
    } catch {
      if (token === loadToken.current) {
        flashNote(failNote);
      }
    }
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
      flashNote("Link copied!");
    } catch {
      pinNote(`Couldn't copy. Here's the link: ${url}`);
    }
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

  async function handleSignOut() {
    setAuthNote("");
    setSigningOut(true);
    // A new key as well as the unmount: the panel that comes back is a fresh one even
    // if React folds the unmount and remount into one render.
    setChatEpoch((n) => n + 1);
    const token = meToken.current;
    const signedOut = await signOutAndForget();
    setSigningOut(false);
    if (!signedOut) {
      // Another tab's Sign out already cleared the cookie and showed it here.
      if (token !== meToken.current) return;
      setAuthNote(
        "Couldn't sign out, so you may still be signed in. Your advisor key and chat were cleared from this tab. Check your connection and try again."
      );
      return;
    }
    showSignedOut();
  }

  function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-importing the same file
    if (!file) return;
    file
      .text()
      .then((text) => saved.importJson(text))
      .catch(() => flashNote("Couldn't read that file."));
  }

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          TF2 <span className="co">Loadout</span> Assistant
        </h1>
        <div className="masthead-side">
          <span className="tagline">Try it on before you trade for it</span>
          {me ? (
            <div className="steam-identity">
              {me.avatar && <img className="steam-avatar" src={me.avatar} alt="" />}
              <span className="steam-persona">{me.persona ?? "Steam user"}</span>
              <button className="steam-signout" onClick={handleSignOut} disabled={signingOut}>
                {signingOut ? "Signing out…" : "Sign out"}
              </button>
            </div>
          ) : (
            <a className="steam-signin" href={steamLoginUrl()} title="We only ever see your SteamID.">
              <img src="/steam/sits_01.png" width={180} height={35} alt="Sign in through Steam" />
            </a>
          )}
        </div>
        {authNote && (
          <p className="auth-note" role="alert">
            {authNote}
          </p>
        )}
        {signInFailed(signIn) && (
          <p className="auth-note" role="alert">
            Couldn't check whether you're signed in.{" "}
            <button type="button" onClick={recheckSignIn} disabled={signInRechecking(signIn)}>
              {signInRechecking(signIn) ? "Checking…" : "Retry"}
            </button>
          </p>
        )}
      </header>

      <div className="bench">
        <section>
          <div className="classbar">
            {CLASSES.map((c) => (
              <button
                key={c}
                className="class-chip"
                aria-pressed={c === cls}
                onClick={() => changeClass(c)}
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

          <FilterBar
            state={filters}
            onChange={setFilters}
            ownedEnabled={canFilterOwned}
            ownedDisabledReason={ownedDisabledReason(backpack)}
            onReloadOwned={backpack.kind === "signed_out" ? undefined : reloadBackpack}
            ownedBusy={backpackBusy(backpack)}
            ownedFailed={backpackFailed(backpack)}
            ownedRefreshFailed={backpackRefreshFailed(backpack)}
          />

          {status === BENCH_UNREACHABLE ? (
            <p className="status error" role="alert">
              {status}{" "}
              <button type="button" onClick={() => setApiRetry((n) => n + 1)}>
                Retry
              </button>
            </p>
          ) : status ? (
            <p className="status">{status}</p>
          ) : entries.length === 0 ? (
            <p className="status">No cosmetics match these filters.</p>
          ) : (
            <div className="grid">
              {entries.map(({ item: c, dimmed, clashesWith }) => (
                <div
                  key={c.defindex}
                  className={[
                    "cell",
                    equippedIds.has(c.defindex) ? "selected" : "",
                    dimmed ? "dimmed" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
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
                  {clashesWith.length > 0 && (
                    <div className="clash-badge">
                      clashes with {clashesWith.join(", ")}
                    </div>
                  )}
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
                      bp.tf ↗
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
              {sharedLoading
                ? "Loading shared loadout…"
                : "Click a cosmetic to equip it. Conflicts get stamped here."}
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
                <button className="save-btn" onClick={saveCurrent} disabled={savedLocked}>
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
          {undoOffered && (
            <p className="share-note" role="status">
              Equipped the advisor's pick.{" "}
              <button onClick={undoAdvisorEquip}>Undo</button>{" "}
              <button onClick={() => setAdvisorUndo(null)} aria-label="Dismiss">
                ✕
              </button>
            </p>
          )}
          {pinnedNotes.map((text) => (
            <p key={text} className="share-note">
              {text}{" "}
              <button onClick={() => unpinNote(text)} aria-label="Dismiss">
                ✕
              </button>
            </p>
          ))}

          <section className="saved">
            <div className="saved-head">
              <h3>Saved loadouts</h3>
              <div className="saved-io">
                <button
                  onClick={exportLoadouts}
                  title="Download all as JSON"
                  disabled={savedLocked}
                >
                  Export
                </button>
                <button
                  onClick={() => importRef.current?.click()}
                  title="Import from JSON"
                  disabled={savedLocked}
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

            {saved.message && (
              <p
                className={saved.message.kind === "error" ? "saved-note error" : "saved-note"}
                role={saved.message.kind === "error" ? "alert" : "status"}
              >
                {saved.message.text}{" "}
                <button onClick={saved.dismissMessage} aria-label="Dismiss">
                  ✕
                </button>
              </p>
            )}

            {saved.status === "loading" ? (
              <p className="status">Loading your saved loadouts…</p>
            ) : saved.status === "error" ? (
              <p className="saved-note error" role="alert">
                Couldn't load the loadouts saved to your Steam account.{" "}
                <button onClick={saved.retry}>Retry</button>
              </p>
            ) : saved.loadouts.length === 0 ? (
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
                        {l.unavailable ? ` · ${l.unavailable} unavailable` : ""}
                      </small>
                    </div>
                    <div className="saved-buttons">
                      <button onClick={() => loadSaved(l.items, l.cls)}>Load</button>
                      <button onClick={() => shareSaved(l.cls, l.items)}>Share</button>
                      <button
                        onClick={() => renameSaved(l.id, l.name)}
                        disabled={savedLocked}
                      >
                        Rename
                      </button>
                      <button
                        className="danger"
                        onClick={() => deleteSaved(l.id, l.name)}
                        disabled={savedLocked}
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {chatAvailable && signInSettled(signIn) && !signingOut && (
            <ChatPanel
              key={chatEpoch}
              viewer={steamId}
              cls={cls}
              loadout={loadout}
              onEquip={equipSuggestion}
            />
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
          . Item and backpack data via the{" "}
          <a href="https://steamcommunity.com/dev" target="_blank" rel="noopener noreferrer">
            Steam Web API
          </a>
          .
        </p>
        <p>
          Team Fortress 2 and all related item images are trademarks of{" "}
          <a href="https://www.valvesoftware.com/" target="_blank" rel="noopener noreferrer">
            Valve Corporation
          </a>
          . This is an unofficial fan project, not affiliated with or endorsed by Valve. Powered by{" "}
          <a href="https://store.steampowered.com/" target="_blank" rel="noopener noreferrer">
            Steam
          </a>
          .
        </p>
      </footer>
    </div>
  );
}
