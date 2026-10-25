/**
 * Sign out, and forget what this tab holds for the player whether or not the server
 * answers.
 *
 * The advisor's history can hold this player's backpack results, and their key bills
 * the player's provider; the next person on this browser should get neither. Neither
 * depends on the server, so both are cleared before the logout request is sent: a
 * request that fails or hangs must not leave them in storage. The caller keeps this
 * tab's advisor unmounted until the request settles (`api.signOut` gives up after
 * `SIGN_OUT_TIMEOUT_MS`, so a hung request settles as a failure), so nothing here can
 * save them again meanwhile; a successful request clears them once more, for anything
 * another tab saved while it was in flight.
 *
 * Other open tabs hold the same player's key and chat, and a tab-only key sits in
 * each tab's own `sessionStorage`, out of this tab's reach. So both steps are
 * announced on a `BroadcastChannel`: "forget" before the request, "signed_out" after
 * a successful one. `listenForSignOut` forgets that tab's copies and lets its caller
 * remount the advisor and, on "signed_out", show the player signed out. A tab
 * restored from the back/forward cache or reopened later missed the message. A
 * session that ends without a Sign out (it expires) is not announced: a re-check's
 * signed-out answer never overrides a signed-in page (`signInCheck.ts`).
 *
 * Resolves `false` when the request failed, so the session cookie may still be live
 * and the caller must keep showing the player as signed in.
 */

import { signOut } from "./api";
import { clearTranscript } from "./chatSession";
import { clearLlmKey } from "./llmKey";

export const SIGN_OUT_CHANNEL = "tf2-sign-out/v1";

/** "forget" when a tab starts signing out, whatever the request then does;
 * "signed_out" once the server has cleared the session cookie every tab shares. */
export type SignOutNews = "forget" | "signed_out";

interface Announcement {
  kind: SignOutNews;
  from: string;
}

// A tab's own listener is a different channel object from the one it announces on,
// so it would hear its own news. Not crypto.randomUUID: that needs a secure context,
// and a LAN-IP dev server isn't one.
const THIS_TAB = Math.random().toString(36).slice(2);

function forget(): void {
  clearTranscript();
  clearLlmKey();
}

function announce(kind: SignOutNews): void {
  try {
    const channel = new BroadcastChannel(SIGN_OUT_CHANNEL);
    const message: Announcement = { kind, from: THIS_TAB };
    channel.postMessage(message);
    channel.close();
  } catch {
    // No BroadcastChannel: other tabs keep what they hold, as before this existed.
  }
}

function newsFrom(data: unknown): SignOutNews | null {
  if (typeof data !== "object" || data === null) return null;
  const { kind, from } = data as Partial<Announcement>;
  if (typeof from !== "string" || from === THIS_TAB) return null;
  return kind === "forget" || kind === "signed_out" ? kind : null;
}

/** Calls `onNews` when another tab of this browser announces a sign-out, after
 * forgetting this tab's own key (a tab-only one included) and transcript, so a panel
 * the caller remounts in response loads empty. Returns the unsubscribe. */
export function listenForSignOut(onNews: (news: SignOutNews) => void): () => void {
  let channel: BroadcastChannel;
  try {
    channel = new BroadcastChannel(SIGN_OUT_CHANNEL);
  } catch {
    return () => {};
  }
  channel.onmessage = (e: MessageEvent) => {
    const news = newsFrom(e.data);
    if (!news) return;
    forget();
    onNews(news);
  };
  return () => channel.close();
}

export async function signOutAndForget(
  request: () => Promise<void> = signOut
): Promise<boolean> {
  forget();
  announce("forget");
  try {
    await request();
  } catch {
    return false;
  }
  // A key or turn another tab saved while the request was pending belongs to the
  // session that just ended.
  forget();
  announce("signed_out");
  return true;
}
