import { useEffect, useRef, useState } from "react";
import {
  fetchChatProviders,
  fetchCosmetic,
  streamChat,
  MAX_MESSAGE_CHARS,
  type ChatProvider,
  type ChatStreamEvent,
  type Cosmetic,
} from "./api";
import {
  EMPTY_TRANSCRIPT,
  appendExchange,
  clearTranscript,
  loadTranscript,
  saveTranscript,
  turnOutcome,
  type Transcript,
} from "./chatSession";
import { clashingIds } from "./filters";
import { clearLlmKey, loadLlmKey, saveLlmKey, type SavedLlmKey } from "./llmKey";

/** What each tool is doing, in the player's terms rather than the function's. */
const TOOL_LABELS: Record<string, string> = {
  search_cosmetics: "Digging through the backpack…",
  get_cosmetic: "Checking an item…",
  check_conflicts: "Making sure it all fits…",
  get_item_lore: "Reading up on it…",
};

interface Props {
  /** Who the page thinks is signed in, read only at mount to pick which saved
   * conversation to show. What a saved conversation is stamped with comes from the
   * server instead (see `chatSession.ts`). */
  viewer: string | null;
  cls: string;
  loadout: Cosmetic[];
  /** `forCls` is the class the suggestion was asked for, when known. */
  onEquip: (items: Cosmetic[], forCls: string | undefined) => void;
}

const GREETING =
  "Tell me the look you're after — \"cop-style Spy\", \"gaudy Australian Sniper\" — and I'll dig through the backpack.";

const KEY_NOTE_SAVED =
  "Saved in this browser until you forget it; signing out forgets it too. Anyone using this browser profile could read it, so on a shared computer keep it for this tab only. It's sent with each message and the server never keeps it.";
const KEY_NOTE_TAB =
  "Kept only in this tab's session storage: it survives a reload, and reopening or duplicating the tab can bring it back. On a shared computer, use Forget key in each copy of the tab before you leave. It's sent with each message and the server never keeps it.";

// The measured figure is in the comment above DEFAULT_MAX_REQUESTS in config.py.
const PER_MESSAGE_NOTE = "Each message makes several model calls, often eight or more.";

export default function ChatPanel({ viewer, cls, loadout, onEquip }: Props) {
  const [transcript, setTranscript] = useState<Transcript>(() => loadTranscript(viewer));
  // The in-flight message: shown in the log, but only joins the transcript with its reply.
  const [pending, setPending] = useState<string | null>(null);
  const busy = pending !== null;
  const [input, setInput] = useState("");
  // The final event has arrived, so the turn will be kept whatever happens: Stop is hidden.
  const [replied, setReplied] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // The player's own provider key; there is no server-side fallback, so with none
  // stored the key form takes the place of the message box.
  const [llmKey, setLlmKey] = useState<SavedLlmKey | null>(() => loadLlmKey());
  const [providers, setProviders] = useState<ChatProvider[]>([]);
  const [providersFailed, setProvidersFailed] = useState(false);
  const [providersAttempt, setProvidersAttempt] = useState(0);
  const [showKeyForm, setShowKeyForm] = useState(llmKey === null);
  const [draftProvider, setDraftProvider] = useState(llmKey?.provider ?? "");
  const [draftKey, setDraftKey] = useState("");
  const [draftRemember, setDraftRemember] = useState(llmKey?.remember ?? true);

  useEffect(() => saveTranscript(transcript), [transcript]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Fetched whenever the form is open and the list is still empty. A failure changes
  // nothing the effect depends on, and with no key stored the form can't be closed and
  // reopened, so a failed load shows a Retry that bumps `providersAttempt`.
  useEffect(() => {
    if (!showKeyForm || providers.length > 0) return;
    let cancelled = false;
    setProvidersFailed(false);
    fetchChatProviders().then((list) => {
      if (cancelled) return;
      if (list.length === 0) {
        setProvidersFailed(true);
        return;
      }
      setProvidersFailed(false);
      setProviders(list);
      // A stored provider the server no longer offers must not stay selected, or
      // every save resends it and the form can never get out of "rejected".
      setDraftProvider((cur) =>
        list.some((p) => p.id === cur) ? cur : list[0].id
      );
    });
    return () => {
      cancelled = true;
    };
  }, [showKeyForm, providers.length, providersAttempt]);

  function retryProviders() {
    setProvidersAttempt((n) => n + 1);
  }

  const providerLabel = (id: string) =>
    providers.find((p) => p.id === id)?.label ?? id;
  const selected = providers.find((p) => p.id === draftProvider);

  // Every way into the form starts from the stored choice, so a checkbox change
  // abandoned with Cancel can't carry into a later save.
  function openKeyForm() {
    setDraftRemember(llmKey?.remember ?? true);
    setShowKeyForm(true);
  }

  function saveKey(e: React.FormEvent) {
    e.preventDefault();
    const apiKey = draftKey.trim();
    if (!draftProvider || !apiKey) return;
    // Header values must be ISO-8859-1; a smart quote copied from a doc would make
    // fetch() throw later with no useful message, so catch it here.
    if (/[^\x20-\x7e]/.test(apiKey)) {
      setError("That key has characters an API key can't contain — paste it again.");
      return;
    }
    const key = { provider: draftProvider, apiKey };
    saveLlmKey(key, draftRemember);
    setLlmKey({ ...key, remember: draftRemember });
    setDraftKey("");
    setError("");
    setShowKeyForm(false);
  }

  function forgetKey() {
    clearLlmKey();
    setLlmKey(null);
    setDraftKey("");
    openKeyForm();
  }

  function rejectKey(provider: string) {
    setError(`${providerLabel(provider)} rejected that key. Check it and try again.`);
    openKeyForm();
  }

  function newChat() {
    clearTranscript();
    setTranscript(EMPTY_TRANSCRIPT);
    setError("");
  }

  // Both guards matter: the abort settles inside this click's microtask checkpoint and
  // React re-renders synchronously, so without them the clicked button could turn back
  // into an enabled submit before the browser runs the click's default action.
  function stopTurn(e: React.MouseEvent) {
    e.preventDefault();
    abortRef.current?.abort();
  }

  // Keep the newest turn in view as the conversation grows.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [transcript.turns, pending]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || !llmKey) return;

    const askedFor = cls;
    const controller = new AbortController();
    abortRef.current = controller;
    setInput("");
    setError("");
    setProgress("");
    setReplied(false);
    setPending(text);

    // The server holds no session, so the class travels with the message; the tray
    // itself travels structurally as defindexes, not flattened into the prompt.
    const prompt = `[Current class: ${askedFor}.]\n${text}`;
    const equipped = loadout.map((c) => c.defindex);

    let final: ChatStreamEvent | null = null;
    let errorEvent: ChatStreamEvent | null = null;
    let thrown: unknown;

    try {
      await streamChat(
        prompt,
        transcript.history,
        equipped,
        llmKey,
        (event) => {
          if (event.kind === "tool") {
            setProgress(TOOL_LABELS[event.name ?? ""] ?? "Working…");
          } else if (event.kind === "final") {
            final = event;
            setReplied(true);
            setProgress("Fetching the picks…");
          } else if (event.kind === "error") {
            errorEvent = event;
          }
        },
        controller.signal
      );
    } catch (err) {
      thrown = err;
    }
    const outcome = turnOutcome({
      final,
      errorEvent,
      thrown,
      aborted: controller.signal.aborted,
    });

    try {
      if (outcome.kind === "reply") {
        const reply = outcome.reply;
        // Re-resolve defindexes so a chip can never name an item that isn't real.
        const items = (
          await Promise.all(
            (reply.suggested_defindexes ?? []).map((d) =>
              fetchCosmetic(d).catch(() => null)
            )
          )
        ).filter((i): i is Cosmetic => i !== null);
        setTranscript((t) =>
          appendExchange(
            t,
            text,
            {
              role: "bot",
              text: reply.message ?? "",
              cls: askedFor,
              suggestions: items,
              conflicts: reply.conflicts ?? [],
            },
            reply.history ?? [],
            reply.steam_id
          )
        );
      } else {
        setInput(text);
        if (outcome.kind === "bad_key") rejectKey(llmKey.provider);
        else if (outcome.kind === "failed") setError(outcome.message);
      }
    } finally {
      setPending(null);
      setReplied(false);
      setProgress("");
      abortRef.current = null;
    }
  }

  return (
    <section className="chat">
      <div className="chat-head">
        <h3>Ask the advisor</h3>
        <div className="chat-head-actions">
          {!busy && transcript.turns.length > 0 && (
            <button type="button" className="chat-key-toggle" onClick={newChat}>
              New chat
            </button>
          )}
          {/* Not mid-turn: the key form replaces the message form, which holds Stop. */}
          {llmKey && !busy && (
            <button
              type="button"
              className="chat-key-toggle"
              onClick={() => (showKeyForm ? setShowKeyForm(false) : openKeyForm())}
              aria-expanded={showKeyForm}
              title={`Using your ${providerLabel(llmKey.provider)} key${
                llmKey.remember ? "" : " for this tab only"
              }`}
            >
              {providerLabel(llmKey.provider)} key
            </button>
          )}
        </div>
      </div>

      <div className="chat-log" ref={logRef}>
        {transcript.turns.length === 0 && !pending && <p className="empty">{GREETING}</p>}

        {transcript.turns.map((turn, i) => {
          const clashing = clashingIds(turn.conflicts ?? []);
          return (
            <div key={i} className={`chat-msg ${turn.role}`}>
              <p>{turn.text}</p>

              {turn.suggestions && turn.suggestions.length > 0 && (
                <div className="chat-suggestions">
                  <div className="chat-chips">
                    {turn.suggestions.map((c) => (
                      <span
                        key={c.defindex}
                        className={`chat-chip${clashing.has(c.defindex) ? " clash" : ""}`}
                        title={c.name}
                      >
                        {c.image_url && <img src={c.image_url} alt="" />}
                        {c.name}
                      </span>
                    ))}
                  </div>
                  {clashing.size > 0 && (
                    <p className="chat-clash-warning">
                      {turn.conflicts!.length} clash
                      {turn.conflicts!.length > 1 ? "es" : ""} in this suggestion —
                      equipping it will still stamp the tray.
                    </p>
                  )}
                  <button
                    className="chat-equip"
                    onClick={() => onEquip(turn.suggestions!, turn.cls)}
                  >
                    {turn.cls && turn.cls !== cls ? `Equip these for ${turn.cls}` : "Equip these"}
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {pending && (
          <div className="chat-msg user">
            <p>{pending}</p>
          </div>
        )}

        {busy && (
          <p className="chat-status">{progress || "Thinking it over…"}</p>
        )}
        {error && <p className="chat-status error">{error}</p>}
      </div>

      {showKeyForm ? (
        <form className="chat-keyform" onSubmit={saveKey}>
          <label className="chat-keyform-row">
            <span>Provider</span>
            <select
              value={draftProvider}
              onChange={(e) => setDraftProvider(e.target.value)}
              disabled={providers.length === 0}
            >
              {providers.length === 0 && (
                <option value="">
                  {providersFailed ? "Couldn't load providers" : "Loading…"}
                </option>
              )}
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label} · {p.model}
                </option>
              ))}
            </select>
          </label>
          {selected && (
            <div className="chat-provider-info">
              <p>
                Runs <strong>{selected.model}</strong>. {selected.note} {PER_MESSAGE_NOTE}
              </p>
              <p>
                <a href={selected.keys_url} target="_blank" rel="noopener noreferrer">
                  Get a key
                </a>
                {" · "}
                <a href={selected.pricing_url} target="_blank" rel="noopener noreferrer">
                  Pricing
                </a>
              </p>
            </div>
          )}
          <label className="chat-keyform-row">
            <span>API key</span>
            <input
              className="search chat-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={llmKey ? "Paste a new key to replace it" : "Paste your key"}
              value={draftKey}
              onChange={(e) => setDraftKey(e.target.value)}
            />
          </label>
          <label className="chat-keyform-check">
            <input
              type="checkbox"
              checked={!draftRemember}
              onChange={(e) => setDraftRemember(!e.target.checked)}
            />
            Keep for this tab only
          </label>
          <p className="chat-key-note">{draftRemember ? KEY_NOTE_SAVED : KEY_NOTE_TAB}</p>
          {providersFailed && (
            <p className="chat-status error" role="alert">
              Couldn't load the list of providers.
            </p>
          )}
          <div className="chat-keyform-actions">
            <button
              className="chat-send"
              type="submit"
              disabled={!draftProvider || !draftKey.trim()}
            >
              Save key
            </button>
            {providersFailed && (
              <button type="button" className="chat-key-secondary" onClick={retryProviders}>
                Retry
              </button>
            )}
            {llmKey && (
              <>
                <button
                  type="button"
                  className="chat-key-secondary"
                  onClick={() => setShowKeyForm(false)}
                >
                  Cancel
                </button>
                <button type="button" className="chat-key-secondary" onClick={forgetKey}>
                  Forget key
                </button>
              </>
            )}
          </div>
        </form>
      ) : (
        <form className="chat-form" onSubmit={submit}>
          <input
            className="search chat-input"
            placeholder={`Describe a ${cls} look…`}
            maxLength={MAX_MESSAGE_CHARS}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          {busy && !replied ? (
            <button key="stop" type="button" className="chat-key-secondary" onClick={stopTurn}>
              Stop
            </button>
          ) : (
            <button
              key="ask"
              className="chat-send"
              type="submit"
              disabled={busy || !input.trim()}
            >
              Ask
            </button>
          )}
        </form>
      )}
    </section>
  );
}
