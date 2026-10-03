// The session store's client when the store can't be reached (store.ts, reconnectDelay): the first
// connection gives up at once, so a request fails now and the next one tries again; a store that
// was there gets node-redis's own backoff (docs/plans/deploy.md, U5c-7).
import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
import { reconnectDelay } from "@/lib/auth/store";

describe("reconnectDelay", () => {
  const refused = new Error("self-signed certificate in certificate chain");

  it("gives up on a store it never reached, with the reason", () => {
    expect(reconnectDelay(false, 0, refused)).toBe(refused);
    expect(reconnectDelay(false, 5, refused)).toBe(refused);
  });

  it("backs off on a store it had reached, as node-redis does, at most 2.2 s", () => {
    for (const [retries, least] of [
      [0, 50],
      [1, 100],
      [3, 400],
      [10, 2000],
    ] as const) {
      const delay = reconnectDelay(true, retries, refused) as number;
      expect(delay).toBeGreaterThanOrEqual(least);
      expect(delay).toBeLessThan(least + 200);
    }
  });
});
