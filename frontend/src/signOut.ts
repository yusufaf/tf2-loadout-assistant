/**
 * Sign out, and forget what this tab holds for the player whether or not the server
 * answers.
 *
 * The advisor's history can hold this player's backpack results, and their key bills
 * the player's provider; the next person on this browser should get neither. Neither
 * depends on the server, so both are cleared before the logout request is sent: a
 * request that fails or hangs must not leave them in storage. The caller keeps this
 * tab's advisor unmounted until the request settles (`api.signOut` gives up after 10 s,
 * so a hung request settles as a failure), so nothing here can save them again
 * meanwhile; a successful request clears them once more, for anything another
 * tab saved while it was in flight.
 *
 * Resolves `false` when the request failed, so the session cookie may still be live
 * and the caller must keep showing the player as signed in.
 */

import { signOut } from "./api";
import { clearTranscript } from "./chatSession";
import { clearLlmKey } from "./llmKey";

function forget(): void {
  clearTranscript();
  clearLlmKey();
}

export async function signOutAndForget(
  request: () => Promise<void> = signOut
): Promise<boolean> {
  forget();
  try {
    await request();
  } catch {
    return false;
  }
  // A key or turn another tab saved while the request was pending belongs to the
  // session that just ended.
  forget();
  return true;
}
