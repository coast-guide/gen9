import { beforeEach, describe, expect, it, vi } from "vitest";

const SECRET_A = "a".repeat(64);
const SECRET_B = "b".repeat(64);

async function load(secret: string) {
  vi.resetModules();
  vi.stubEnv("APP_URL", "http://localhost:14000");
  vi.stubEnv("KEYCLOAK_ISSUER", "http://localhost:15000/realms/gen9");
  vi.stubEnv("KEYCLOAK_CLIENT_ID", "gen9-ui");
  vi.stubEnv("KEYCLOAK_CLIENT_SECRET", "test");
  vi.stubEnv("SESSION_SECRET", secret);
  vi.stubEnv("SESSION_STORE_URL", "redis://localhost:6379");
  vi.stubEnv("GEN9_AGENT_URL", "http://localhost:17000");
  return import("./crypto");
}

describe("session record sealing (AES-256-GCM)", () => {
  beforeEach(() => vi.unstubAllEnvs());

  it("round-trips a record", async () => {
    const { seal, unseal } = await load(SECRET_A);
    const record = { sub: "user-1", refreshToken: "secret-refresh-token" };
    const sealed = seal(record);
    expect(sealed).not.toContain("secret-refresh-token");
    expect(unseal(sealed)).toEqual(record);
  });

  it("uses a fresh IV every time", async () => {
    const { seal } = await load(SECRET_A);
    expect(seal({ a: 1 })).not.toBe(seal({ a: 1 }));
  });

  it("rejects a tampered record", async () => {
    const { seal, unseal } = await load(SECRET_A);
    const raw = Buffer.from(seal({ sub: "user-1" }), "base64url");
    raw[raw.length - 1] ^= 0x01;
    expect(unseal(raw.toString("base64url"))).toBeNull();
  });

  it("rejects records sealed with another secret (rotation signs everyone out)", async () => {
    const sealed = (await load(SECRET_A)).seal({ sub: "user-1" });
    expect((await load(SECRET_B)).unseal(sealed)).toBeNull();
  });
});

describe("session ids", () => {
  it("are 256-bit, URL-safe and unique", async () => {
    const { randomId } = await load(SECRET_A);
    const ids = new Set(Array.from({ length: 1000 }, randomId));
    expect(ids.size).toBe(1000);
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("are stored as a hash, never as the cookie value", async () => {
    const { hashId, randomId } = await load(SECRET_A);
    const id = randomId();
    expect(hashId(id)).not.toBe(id);
    expect(hashId(id)).toBe(hashId(id));
  });
});
