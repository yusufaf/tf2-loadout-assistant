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
 * remount the advisor and, on "signed_out", show the player signed out. A session
 * that ends without a Sign out (it expires) is not announced: a re-check's signed-out
 * answer never overrides a signed-in page (`signInCheck.ts`).
 *
 * A tab that wasn't listening misses the announcement: one closed before the sign-out
 * and reopened later gets its `sessionStorage` back, and one in the back/forward cache
 * comes back with its React state. So each sign-out also writes a mark to
 * `localStorage` (and a fresh one once the request succeeds), carried in the
 * announcements too, and every tab keeps the mark it has caught up with in its own
 * `sessionStorage`. A tab whose copy differs, checked when the page starts and on a
 * back/forward-cache `pageshow`, clears only its own tab-only key: by then the shared
 * key and transcript may be the next person's, and the signing-out tab already cleared
 * the old ones. Storage that can't be read counts as missed.
 *
 * Resolves `false` when the request failed, so the session cookie may still be live
 * and the caller must keep showing the player as signed in.
 */

import { signOut } from "./api";
import { clearTranscript } from "./chatSession";
import { clearLlmKey, clearTabOnlyLlmKey } from "./llmKey";

export const SIGN_OUT_CHANNEL = "tf2-sign-out/v1";

/** The latest sign-out in `localStorage`; the one this tab caught up with in its
 * `sessionStorage`. */
export const SIGN_OUT_MARK_KEY = "tf2-sign-out-mark/v1";

/** "forget" when a tab starts signing out, whatever the request then does;
 * "signed_out" once the server has cleared the session cookie every tab shares. */
export type SignOutNews = "forget" | "signed_out";

/** Why a tab caught up: a sign-out it missed, or storage it couldn't read. */
export type MissedSignOut = "signed_out" | "unreadable";

interface Announcement {
  kind: SignOutNews;
  from: string;
  /** The mark written for this announcement; absent when the write failed. */
  mark?: string;
}

/** An id per sign-out; null when this browser has none recorded. */
type Mark = string | null;

// A tab's own listener is a different channel object from the one it announces on,
// so it would hear its own news. Not crypto.randomUUID: that needs a secure context,
// and a LAN-IP dev server isn't one.
const THIS_TAB = Math.random().toString(36).slice(2);

// Thunks, as in llmKey.ts, so a store that isn't defined throws inside the try.
const shared = () => localStorage;
const thisTab = () => sessionStorage;

/** Undefined when the store can't be read. */
function readMark(store: () => Storage): Mark | undefined {
  try {
    return store().getItem(SIGN_OUT_MARK_KEY);
  } catch {
    return undefined;
  }
}

function rememberSeen(mark: Mark): void {
  try {
    if (mark === null) thisTab().removeItem(SIGN_OUT_MARK_KEY);
    else thisTab().setItem(SIGN_OUT_MARK_KEY, mark);
  } catch {
    // Unreadable next time too, which counts as missed: nothing kept by mistake.
  }
}

/** Writes a new mark and records it as seen here; undefined when it couldn't be
 * written, in which case nothing is recorded either. */
function markSignOut(): string | undefined {
  const id = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  try {
    shared().setItem(SIGN_OUT_MARK_KEY, id);
  } catch {
    return undefined;
  }
  rememberSeen(id);
  return id;
}

export function missedSignOut(
  latest: Mark | undefined,
  seen: Mark | undefined
): MissedSignOut | null {
  if (latest === undefined || seen === undefined) return "unreadable";
  return latest !== seen ? "signed_out" : null;
}

/** Clears this tab's own tab-only key if it missed a sign-out, and says why. */
export function catchUpWithSignOut(): MissedSignOut | null {
  const latest = readMark(shared);
  const why = missedSignOut(latest, readMark(thisTab));
  if (!why) return null;
  clearTabOnlyLlmKey();
  if (latest !== undefined) rememberSeen(latest);
  return why;
}

/** Calls `onMissed` when this tab comes back from the back/forward cache having
 * missed a sign-out, after clearing its tab-only key. Returns the unsubscribe. */
export function listenForMissedSignOut(onMissed: (why: MissedSignOut) => void): () => void {
  const onShow = (e: Event) => {
    if (!(e as PageTransitionEvent).persisted) return;
    const why = catchUpWithSignOut();
    if (why) onMissed(why);
  };
  try {
    window.addEventListener("pageshow", onShow);
  } catch {
    return () => {};
  }
  return () => window.removeEventListener("pageshow", onShow);
}

function forget(): void {
  clearTranscript();
  clearLlmKey();
}

function announce(kind: SignOutNews, mark?: string): void {
  try {
    const channel = new BroadcastChannel(SIGN_OUT_CHANNEL);
    const message: Announcement = { kind, from: THIS_TAB };
    if (mark !== undefined) message.mark = mark;
    channel.postMessage(message);
    channel.close();
  } catch {
    // No BroadcastChannel: other tabs keep what they hold, as before this existed.
  }
}

function newsFrom(data: unknown): { news: SignOutNews; mark?: string } | null {
  if (typeof data !== "object" || data === null) return null;
  const { kind, from, mark } = data as Partial<Announcement>;
  if (typeof from !== "string" || from === THIS_TAB) return null;
  if (kind !== "forget" && kind !== "signed_out") return null;
  return typeof mark === "string" ? { news: kind, mark } : { news: kind };
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
    const heard = newsFrom(e.data);
    if (!heard) return;
    forget();
    // From the message, not localStorage: the sender's write may not have landed here
    // yet. Caught up, so a key the next person saves in this tab isn't cleared later.
    if (heard.mark !== undefined) rememberSeen(heard.mark);
    onNews(heard.news);
  };
  return () => channel.close();
}

export async function signOutAndForget(
  request: () => Promise<void> = signOut
): Promise<boolean> {
  // Forget first: a full localStorage gets the transcript's space back for the mark.
  forget();
  const mark = markSignOut();
  announce("forget", mark);
  try {
    await request();
  } catch {
    return false;
  }
  // A key or turn another tab saved while the request was pending belongs to the
  // session that just ended, and a fresh mark makes a tab that missed this second
  // announcement catch up.
  forget();
  announce("signed_out", markSignOut() ?? mark);
  return true;
}
