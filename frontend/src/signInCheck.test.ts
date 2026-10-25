import { describe, expect, it } from "vitest";
import type { Me } from "./api";
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

const ME: Me = { signed_in: true, steam_id: "76561190000000001", persona: "p", avatar: null };
const OUT: Me = { signed_in: false };

const CHECKING: SignInState = { kind: "checking" };
const SIGNED_OUT: SignInState = { kind: "signed_out" };
const UNKNOWN: SignInState = { kind: "unknown", rechecking: false };
const RECHECKING: SignInState = { kind: "unknown", rechecking: true };
const SIGNED_IN: SignInState = { kind: "signed_in", me: ME };
const STATES: SignInState[] = [CHECKING, SIGNED_OUT, UNKNOWN, RECHECKING, SIGNED_IN];

describe("settleSignIn", () => {
  it("a signed-in answer wins from any state", () => {
    for (const s of STATES) {
      expect(settleSignIn(s, ME)).toEqual({ kind: "signed_in", me: ME });
    }
  });

  it("a signed-out answer never overrides a signed-in page", () => {
    // Detecting an expired session is out of scope.
    expect(settleSignIn(SIGNED_IN, OUT)).toBe(SIGNED_IN);
  });

  it("a signed-out answer replaces checking or unknown", () => {
    for (const s of [CHECKING, UNKNOWN, RECHECKING, SIGNED_OUT]) {
      expect(settleSignIn(s, OUT)).toEqual({ kind: "signed_out" });
    }
  });

  it("a probe that couldn't tell never replaces a real answer", () => {
    expect(settleSignIn(SIGNED_IN, null)).toBe(SIGNED_IN);
    expect(settleSignIn(SIGNED_OUT, null)).toBe(SIGNED_OUT);
  });

  it("a probe that couldn't tell is unknown, and its Retry settles", () => {
    for (const s of [CHECKING, UNKNOWN, RECHECKING]) {
      expect(settleSignIn(s, null)).toEqual({ kind: "unknown", rechecking: false });
    }
  });

  it("an unknown answer is never signed in", () => {
    for (const s of [CHECKING, SIGNED_OUT, UNKNOWN, RECHECKING]) {
      expect(signedInAs(settleSignIn(s, null))).toBeNull();
    }
  });
});

describe("recheckRequest", () => {
  it("re-checks an unknown state, keeping the note up while it runs", () => {
    expect(recheckRequest(UNKNOWN)).toEqual({ kind: "unknown", rechecking: true });
  });

  it("does nothing while a re-check is in flight or there's an answer", () => {
    for (const s of [RECHECKING, CHECKING, SIGNED_OUT, SIGNED_IN]) {
      expect(recheckRequest(s)).toBeNull();
    }
  });
});

describe("helpers", () => {
  it("signInSettled is false only while the first probe is out", () => {
    expect(STATES.map(signInSettled)).toEqual([false, true, true, true, true]);
  });

  it("signedInAs names only a signed-in player with a SteamID", () => {
    expect(STATES.map(signedInAs)).toEqual([null, null, null, null, ME.steam_id]);
    expect(signedInAs({ kind: "signed_in", me: { signed_in: true } })).toBeNull();
  });

  it("signedInMe is the answer only when signed in", () => {
    expect(STATES.map(signedInMe)).toEqual([null, null, null, null, ME]);
  });

  it("signInFailed is true for both unknown states", () => {
    expect(STATES.map(signInFailed)).toEqual([false, false, true, true, false]);
  });

  it("signInRechecking is true only while a re-check runs", () => {
    expect(STATES.map(signInRechecking)).toEqual([false, false, false, true, false]);
  });
});
