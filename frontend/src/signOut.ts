/**
 * Sign out, and forget what this tab holds for the player whether or not the server
 * answers.
 *
 * The advisor's history can hold this player's backpack results, and their key bills
 * the player's provider; the next person on this browser should get neither. Neither
 * depends on the server, so both are cleared before the logout request is sent: a
 * request that fails or hangs must not leave them in storage. A successful request
 * clears them again, for anything saved while it was in flight.
 *
 * Resolves `false` when the request failed, so the session cookie may still be live
 * and the caller must keep showing the player as signed in.
 */

import { signOut } from "./api";
import { clearTranscript } from "./chatSession";
import { clearLlmKey } from "./llmKey";

export async function signOutAndForget(
  request: () => Promise<void> = signOut
): Promise<boolean> {
  clearTranscript();
  clearLlmKey();
  try {
    await request();
  } catch {
    return false;
  }
  // The player was still signed in while the request was pending, so a key or turn
  // saved meanwhile belongs to the session that just ended.
  clearTranscript();
  clearLlmKey();
  return true;
}
