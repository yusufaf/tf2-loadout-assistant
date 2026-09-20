import { useEffect, useRef, useState } from "react";
import {
  fetchChatProviders,
  fetchCosmetic,
  streamChat,
  ChatUnavailableError,
  LlmKeyError,
  type ChatProvider,
  type ChatStreamEvent,
  type Conflict,
  type Cosmetic,
} from "./api";
import { clashingIds } from "./filters";
import { clearLlmKey, loadLlmKey, saveLlmKey, type LlmKey } from "./llmKey";

/** What each tool is doing, in the player's terms rather than the function's. */
const TOOL_LABELS: Record<string, string> = {
  search_cosmetics: "Digging through the backpack…",
  get_cosmetic: "Checking an item…",
  check_conflicts: "Making sure it all fits…",
  get_item_lore: "Reading up on it…",
};

interface Turn {
  role: "user" | "bot";
  text: string;
  /** Items the agent recommended, already resolved against the real catalog. */
  suggestions?: Cosmetic[];
  /** Clashes within the suggested set, server-checked even if the agent skipped it. */
  conflicts?: Conflict[];
}

interface Props {
  cls: string;
  loadout: Cosmetic[];
  onEquip: (items: Cosmetic[]) => void;
}

const GREETING =
  "Tell me the look you're after — \"cop-style Spy\", \"gaudy Australian Sniper\" — and I'll dig through the backpack.";

const KEY_NOTE =
  "Your key stays in this browser. It's sent with each message and the server never keeps it.";

export default function ChatPanel({ cls, loadout, onEquip }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  // The player's own provider key; there is no server-side fallback, so with none
  // stored the key form takes the place of the message box.
  const [llmKey, setLlmKey] = useState<LlmKey | null>(() => loadLlmKey());
  const [providers, setProviders] = useState<ChatProvider[]>([]);
  const [showKeyForm, setShowKeyForm] = useState(llmKey === null);
  const [draftProvider, setDraftProvider] = useState(llmKey?.provider ?? "");
  const [draftKey, setDraftKey] = useState("");

  // Fetched whenever the form is open and the list is still empty, so a failed
  // first load recovers on the next open instead of leaving "Loading…" forever.
  useEffect(() => {
    if (!showKeyForm || providers.length > 0) return;
    let cancelled = false;
    fetchChatProviders().then((list) => {
      if (cancelled || list.length === 0) return;
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
  }, [showKeyForm, providers.length]);

  const providerLabel = (id: string) =>
    providers.find((p) => p.id === id)?.label ?? id;

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
    saveLlmKey(key);
    setLlmKey(key);
    setDraftKey("");
    setError("");
    setShowKeyForm(false);
  }

  function forgetKey() {
    clearLlmKey();
    setLlmKey(null);
    setDraftKey("");
    setShowKeyForm(true);
  }

  function rejectKey(provider: string) {
    setError(`${providerLabel(provider)} rejected that key. Check it and try again.`);
    setShowKeyForm(true);
  }

  // Keep the newest turn in view as the conversation grows.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || !llmKey) return;

    setInput("");
    setError("");
    setProgress("");
    setTurns((t) => [...t, { role: "user", text }]);
    setBusy(true);

    // The server holds no session, so the class travels with the message; the tray
    // itself travels structurally as defindexes, not flattened into the prompt.
    const prompt = `[Current class: ${cls}.]\n${text}`;
    const equipped = loadout.map((c) => c.defindex);

    let final: ChatStreamEvent | null = null;
    let failure = "";
    let badKey = false;

    try {
      await streamChat(prompt, history, equipped, llmKey, (event) => {
        if (event.kind === "tool") {
          setProgress(TOOL_LABELS[event.name ?? ""] ?? "Working…");
        } else if (event.kind === "final") {
          final = event;
        } else if (event.kind === "error") {
          badKey = event.code === "bad_key";
          failure = event.detail ?? "The advisor didn't answer. Try again?";
        }
      });

      if (badKey) {
        rejectKey(llmKey.provider);
      } else if (failure || !final) {
        setError(failure || "The advisor didn't answer. Try again?");
      } else {
        const reply: ChatStreamEvent = final;
        setHistory(reply.history ?? []);
        // Re-resolve defindexes so a chip can never name an item that isn't real.
        const items = (
          await Promise.all(
            (reply.suggested_defindexes ?? []).map((d) =>
              fetchCosmetic(d).catch(() => null)
            )
          )
        ).filter((i): i is Cosmetic => i !== null);
        setTurns((t) => [
          ...t,
          {
            role: "bot",
            text: reply.message ?? "",
            suggestions: items,
            conflicts: reply.conflicts ?? [],
          },
        ]);
      }
    } catch (err) {
      if (err instanceof LlmKeyError) {
        rejectKey(llmKey.provider);
      } else {
        setError(
          err instanceof ChatUnavailableError
            ? "Chat is switched off on the server right now."
            : "The advisor didn't answer. Try again?"
        );
      }
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  return (
    <section className="chat">
      <div className="chat-head">
        <h3>Ask the advisor</h3>
        {llmKey && (
          <button
            type="button"
            className="chat-key-toggle"
            onClick={() => setShowKeyForm((v) => !v)}
            aria-expanded={showKeyForm}
            title={`Using your ${providerLabel(llmKey.provider)} key`}
          >
            {providerLabel(llmKey.provider)} key
          </button>
        )}
      </div>

      <div className="chat-log" ref={logRef}>
        {turns.length === 0 && <p className="empty">{GREETING}</p>}

        {turns.map((turn, i) => {
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
                    onClick={() => onEquip(turn.suggestions!)}
                  >
                    Equip these
                  </button>
                </div>
              )}
            </div>
          );
        })}

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
              {providers.length === 0 && <option value="">Loading…</option>}
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label} · {p.model}
                </option>
              ))}
            </select>
          </label>
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
          <p className="chat-key-note">{KEY_NOTE}</p>
          <div className="chat-keyform-actions">
            <button
              className="chat-send"
              type="submit"
              disabled={!draftProvider || !draftKey.trim()}
            >
              Save key
            </button>
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
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          <button className="chat-send" type="submit" disabled={busy || !input.trim()}>
            Ask
          </button>
        </form>
      )}
    </section>
  );
}
