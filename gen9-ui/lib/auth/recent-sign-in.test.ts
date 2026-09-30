import { describe, expect, it } from "vitest";

import { RECENT_SIGN_IN_S, signedInRecently } from "./recent-sign-in";

const now = Date.UTC(2026, 8, 24, 12, 0, 0);
const secondsAgo = (s: number) => now / 1000 - s;

describe("signedInRecently", () => {
  it("accepts a sign-in inside the window", () => {
    expect(signedInRecently(secondsAgo(10), now)).toBe(true);
    expect(signedInRecently(secondsAgo(RECENT_SIGN_IN_S), now)).toBe(true);
  });

  it("rejects an older or unknown sign-in", () => {
    expect(signedInRecently(secondsAgo(RECENT_SIGN_IN_S + 1), now)).toBe(false);
    expect(signedInRecently(null, now)).toBe(false);
  });
});
