/**
 * What the page knows about who is signed in, outside `App.tsx` so the rules test
 * without a DOM.
 *
 * `fetchMe` answers signed in, signed out, or `null` when it couldn't tell. `unknown` is
 * its own state, never a signed-out one, so the page can say the check failed and offer
 * a Retry. It still grants nothing: only `signed_in` names a viewer, so the backpack,
 * saved loadouts and the advisor treat it as signed out. An answer that couldn't be got
 * never replaces one that was. A signed-out answer never replaces a signed-in page,
 * because detecting an expired session is out of scope. A re-check stays `unknown` while
 * in flight, so the note and its Retry stay put, disabled.
 */

import type { Me } from "./api";

export type SignInState =
  | { kind: "checking" }
  | { kind: "signed_out" }
  | { kind: "unknown"; rechecking: boolean }
  | { kind: "signed_in"; me: Me };

/** Folds a finished probe (`null` when it couldn't tell) into the state it was made from. */
export function settleSignIn(prev: SignInState, probe: Me | null): SignInState {
  if (probe?.signed_in) return { kind: "signed_in", me: probe };
  if (prev.kind === "signed_in") return prev;
  if (probe) return { kind: "signed_out" };
  return prev.kind === "signed_out" ? prev : { kind: "unknown", rechecking: false };
}

/** The state to show while re-checking; null when there's nothing to re-check or a
 * re-check is already in flight. */
export function recheckRequest(s: SignInState): SignInState | null {
  return s.kind === "unknown" && !s.rechecking ? { kind: "unknown", rechecking: true } : null;
}

/** The first probe has settled, with an answer or `unknown`. */
export function signInSettled(s: SignInState): boolean {
  return s.kind !== "checking";
}

export function signedInMe(s: SignInState): Me | null {
  return s.kind === "signed_in" ? s.me : null;
}

export function signedInAs(s: SignInState): string | null {
  return s.kind === "signed_in" && s.me.steam_id ? s.me.steam_id : null;
}

export function signInFailed(s: SignInState): boolean {
  return s.kind === "unknown";
}

export function signInRechecking(s: SignInState): boolean {
  return s.kind === "unknown" && s.rechecking;
}
