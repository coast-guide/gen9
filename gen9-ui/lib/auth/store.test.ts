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

// The user index ("sign out everywhere", back-channel logout by user) lives as long as the user's
// longest session and an hour more, not a fixed 30 days: once the last session ends, it goes too
// (docs/plans/manual-e2e.md, P8-Z1)
describe("saveSession", () => {
  it("gives the user index its longest session's lifetime, and an hour more", async () => {
    const calls: unknown[][] = [];
    const multi: Record<string, (...args: unknown[]) => unknown> = {};
    for (const name of ["set", "sAdd", "sRem", "expire"]) {
      multi[name] = (...args: unknown[]) => {
        calls.push([name, ...args]);
        return multi;
      };
    }
    multi.exec = async () => [];
    const client = { sMembers: async () => [], mGet: async () => [], multi: () => multi };
    vi.resetModules();
    vi.doMock("redis", () => ({ createClient: () => ({ on: () => ({ on: () => ({ connect: async () => client }) }) }) }));
    vi.stubEnv("APP_URL", "http://localhost:14000");
    vi.stubEnv("KEYCLOAK_ISSUER", "http://localhost:15000/realms/gen9");
    vi.stubEnv("KEYCLOAK_CLIENT_ID", "gen9-ui");
    vi.stubEnv("KEYCLOAK_CLIENT_SECRET", "test");
    vi.stubEnv("SESSION_SECRET", "a".repeat(64));
    vi.stubEnv("SESSION_STORE_URL", "redis://localhost:6379");
    vi.stubEnv("GEN9_AGENT_URL", "http://localhost:17000");
    delete (globalThis as { gen9SessionStore?: unknown }).gen9SessionStore;
    const { saveSession } = await import("@/lib/auth/store");

    const now = Date.now();
    await saveSession("session-id", {
      sub: "user-1",
      sid: null,
      email: null,
      name: null,
      givenName: null,
      roles: [],
      accessToken: "a",
      accessTokenExpiresAt: now + 300_000,
      refreshToken: "r",
      refreshTokenExpiresAt: now + 1_800_000,
      idToken: "i",
      authTime: null,
      createdAt: now,
    });

    const index = calls.filter(([name, key]) => name === "expire" && key === "gen9:session-by-sub:user-1");
    expect(index.map(([, , , mode]) => mode)).toEqual(["NX", "GT"]);
    for (const [, , seconds] of index) {
      expect(seconds).toBeGreaterThanOrEqual(1_800 + 3_600 - 1);
      expect(seconds).toBeLessThanOrEqual(1_800 + 3_600);
    }
    vi.doUnmock("redis");
    vi.unstubAllEnvs();
  });
});
