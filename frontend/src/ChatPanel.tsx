import { useEffect, useRef, useState } from "react";
import {
  fetchCosmetic,
  streamChat,
  ChatUnavailableError,
  type ChatStreamEvent,
  type Cosmetic,
} from "./api";

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
}

interface Props {
  cls: string;
  loadout: Cosmetic[];
  onEquip: (items: Cosmetic[]) => void;
}

const GREETING =
  "Tell me the look you're after — \"cop-style Spy\", \"gaudy Australian Sniper\" — and I'll dig through the backpack.";

export default function ChatPanel({ cls, loadout, onEquip }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  // Keep the newest turn in view as the conversation grows.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, busy]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    setInput("");
    setError("");
    setProgress("");
    setTurns((t) => [...t, { role: "user", text }]);
    setBusy(true);

    // The server holds no session, so the bench state travels with the message.
    const equipped = loadout.map((c) => c.name).join(", ") || "nothing yet";
    const prompt = `[Current class: ${cls}. Equipped: ${equipped}.]\n${text}`;

    let final: ChatStreamEvent | null = null;
    let failure = "";

    try {
      await streamChat(prompt, history, (event) => {
        if (event.kind === "tool") {
          setProgress(TOOL_LABELS[event.name ?? ""] ?? "Working…");
        } else if (event.kind === "final") {
          final = event;
        } else if (event.kind === "error") {
          failure = event.detail ?? "The advisor didn't answer. Try again?";
        }
      });

      if (failure || !final) {
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
          { role: "bot", text: reply.message ?? "", suggestions: items },
        ]);
      }
    } catch (err) {
      setError(
        err instanceof ChatUnavailableError
          ? "Chat is switched off — no LLM key configured."
          : "The advisor didn't answer. Try again?"
      );
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  return (
    <section className="chat">
      <h3>Ask the advisor</h3>

      <div className="chat-log" ref={logRef}>
        {turns.length === 0 && <p className="empty">{GREETING}</p>}

        {turns.map((turn, i) => (
          <div key={i} className={`chat-msg ${turn.role}`}>
            <p>{turn.text}</p>

            {turn.suggestions && turn.suggestions.length > 0 && (
              <div className="chat-suggestions">
                <div className="chat-chips">
                  {turn.suggestions.map((c) => (
                    <span key={c.defindex} className="chat-chip" title={c.name}>
                      {c.image_url && <img src={c.image_url} alt="" />}
                      {c.name}
                    </span>
                  ))}
                </div>
                <button
                  className="chat-equip"
                  onClick={() => onEquip(turn.suggestions!)}
                >
                  Equip these
                </button>
              </div>
            )}
          </div>
        ))}

        {busy && (
          <p className="chat-status">{progress || "Thinking it over…"}</p>
        )}
        {error && <p className="chat-status error">{error}</p>}
      </div>

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
    </section>
  );
}
